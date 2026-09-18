# BaSyx operation delegation for the OIP simulation

BaSyx Operation Delegation is the boundary between standardized AAS Operation
invocation and controller-facing MQTT commands. The Python agent invokes the
Operation through BaSyx; BaSyx forwards the body to the URL in the Operation's
`invocationDelegation` qualifier.

```text
Python agent -> BaSyx `/invoke` -> delegation HTTP endpoint
             -> MQTT operation command -> OIP controller
             -> MQTT completion/fault reply -> Python agent
```

An HTTP success from the delegation service means that the command was
published. It does not mean that the physical or simulated operation has
completed.

## Delegation endpoints

The current Spring Boot adapter exposes:

| Operation | POST endpoint | MQTT topic |
|---|---|---|
| Set conveyor running | `/simulation/stations/{stationId}/conveyorbelt/run` | `simulation/{stationId}/operations/conveyorRunning` |
| Set conveyor speed | `/simulation/stations/{stationId}/conveyorbelt/speed` | `simulation/{stationId}/operations/conveyorSpeed` |
| Move a box | `/simulation/robots/{robotId}/movebox` | `simulation/robots/{robotId}/operations/moveBox` |
| Move robot home | `/simulation/stations/{stationId}/robot/move-to-home` | `simulation/{stationId}/operations/MoveToHome` |

There is no generic `/operation/invoke` endpoint.

### Bundled AASX compatibility

The `ExecuteMoveBox` qualifiers in `aas/robot01.aasx` and `aas/robot02.aasx`
use the supported robot-addressed endpoints for `Robot_01` and `Robot_02`.
These are the operations used by the primary path. Other bundled qualifiers
are not currently aligned with this adapter:

- conveyor qualifiers use `/simulation/conveyors/{conveyorId}/...`, while the
  adapter currently exposes station-addressed conveyor endpoints;
- the robot move-home qualifier uses
  `/simulation/robots/{robotId}/move-to-home`, while the adapter currently
  exposes the station-addressed endpoint; and
- the bundled `convey-workpiece` qualifier has no corresponding controller
  endpoint.

Those paths return HTTP 404 at the adapter; a call through BaSyx may surface
the downstream failure as HTTP 424. Aligning the URL alone is insufficient
for conveyor and move-home calls: their input-format limits are described below.

Use the container DNS name in AAS qualifiers. For example, Robot 01's
`MoveBox` qualifier is:

```json
{
  "type": "invocationDelegation",
  "value": "http://operation-delegation-service:8087/simulation/robots/Robot_01/movebox"
}
```

The qualifier type is case-sensitive and must be `invocationDelegation`. The
target must be reachable from `aas-env`.

## MoveBox contract

`MoveBox` is the operation used by the primary semantic orchestration path.
Its AAS Operation must declare two input variables with these semantic IDs:

1. `urn:agent-aas:semantics:SourceTransferLocation:1`
2. `urn:agent-aas:semantics:TargetTransferLocation:1`

Their `idShort` values may vary. The agent maps values using the semantic IDs
and adds `requestId` and `runId` metadata inputs. The adapter accepts wrapped
variables in either `inputArguments` or `inputVariables` (`inputVariables`
takes precedence if both are present). For either missing semantic value, it
falls back to `SourcePosition` or `TargetPosition` in top-level fields, named
variables, or `params`.

Example BaSyx delegation request:

```json
{
  "inputArguments": [
    {
      "value": {
        "modelType": "Property",
        "idShort": "Source",
        "valueType": "xs:string",
        "value": "urn:agent-aas:asset-instance:conveyor01",
        "semanticId": {
          "type": "ExternalReference",
          "keys": [{
            "type": "GlobalReference",
            "value": "urn:agent-aas:semantics:SourceTransferLocation:1"
          }]
        }
      }
    },
    {
      "value": {
        "modelType": "Property",
        "idShort": "Target",
        "valueType": "xs:string",
        "value": "urn:agent-aas:entity:oip-factory01:pallet01",
        "semanticId": {
          "type": "ExternalReference",
          "keys": [{
            "type": "GlobalReference",
            "value": "urn:agent-aas:semantics:TargetTransferLocation:1"
          }]
        }
      }
    },
    {"value":{"idShort":"requestId","value":"job-42"}},
    {"value":{"idShort":"runId","value":"experiment-7"}}
  ],
  "inoutputArguments": [],
  "requestedTimeout": 8000
}
```

The resulting MQTT command is:

```json
{
  "requestId": "job-42",
  "runId": "experiment-7",
  "operation": "moveBox",
  "params": {
    "SourcePosition": "urn:agent-aas:asset-instance:conveyor01",
    "TargetPosition": "urn:agent-aas:entity:oip-factory01:pallet01"
  }
}
```

Important details:

- The topic selects the robot; the payload has no `robotId`.
- The request and payload have no `stationId` requirement for `MoveBox`.
- Canonical source and target identities are preserved unchanged. The adapter
  does not map them to simulator-local names.
- If `requestId` is absent, the adapter generates a UUID. If `runId` is absent,
  it publishes an empty string.
- A missing source or target returns HTTP 500 with `status: "ERROR"`.

The controller must use the same `requestId` in its lifecycle replies.

## Conveyor and move-home inputs

Use JSON objects with `running`, `speed`, or `move`, respectively, plus an
optional `requestId`. The station comes from the endpoint path. These endpoints
also accept `value`, wrapped `inputVariables`, or an array of wrapped variables.

Their value parsers do not support `inputArguments`: with only that wrapper,
speed returns HTTP 500, while running and move-home interpret the command as
`false`. Use the supported formats even after correcting a bundled qualifier
URL. Full conversion rules are in the
[service README](operation-delegation-service/README.md#other-input-formats).

Examples:

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8087/simulation/stations/Station_01/conveyorbelt/run" `
  -Method Post -ContentType "application/json" `
  -Body '{"running":true,"requestId":"run-1"}'

Invoke-RestMethod `
  -Uri "http://localhost:8087/simulation/stations/Station_01/conveyorbelt/speed" `
  -Method Post -ContentType "application/json" `
  -Body '{"speed":55.0,"requestId":"speed-1"}'

Invoke-RestMethod `
  -Uri "http://localhost:8087/simulation/robots/Robot_01/movebox" `
  -Method Post -ContentType "application/json" `
  -Body '{"SourcePosition":"urn:source","TargetPosition":"urn:target","requestId":"move-1"}'
```

## Completion and fault replies

The Python agent listens on `simulation/+/replies/+`. A controller reply must
be a JSON object with the delegated `requestId` and either `status` or boolean
`success`:

```json
{"requestId":"job-42","status":"completed"}
```

For example, publish on `simulation/Robot_01/replies/moveBox`. The command topic
has an extra `robots` segment; `simulation/robots/Robot_01/replies/moveBox` does
not match the default reply subscription.

- Non-terminal: `started`, `running`, `accepted`
- Successful terminal: `completed`, `complete`, `succeeded`, `success`
- Failed terminal: `failed`, `fault`, `faulted`, `error`

On a terminal reply or timeout, the agent records the result and releases the
reserved resource. Unknown request IDs and unsupported statuses are ignored.
`OPERATION_TIMEOUT_SECONDS` defaults to 60 seconds and starts after a successful
HTTP invocation returns. Non-terminal replies do not extend it. Releasing a
reservation on timeout does not send a stop command to the controller; robot
state telemetry must still accurately report movement and availability.

Only jobs submitted by the agent are tracked this way. A direct HTTP test of
the adapter publishes a command without creating an orchestrator job.

## BaSyx allowlist

Delegation target validation is configured in
[basyx/aas-env.properties](basyx/aas-env.properties):

```properties
basyx.submodelrepository.feature.operation.delegation.security.allowlist.hosts=operation-delegation-service
basyx.submodelrepository.feature.operation.delegation.security.allowlist.ports=8087
```

Without this allowlist, BaSyx may return HTTP 424 for the private delegation
target.

## Run and troubleshoot

```powershell
docker compose up -d --build mosquitto operation-delegation-service
docker compose logs -f aas-env operation-delegation-service mosquitto python-agent
```

- HTTP 404: the qualifier path does not match one of the endpoint paths above.
- HTTP 424 from BaSyx: inspect the allowlist, delegation service availability,
  and the downstream HTTP response.
- HTTP 200 but no simulated action: inspect the MQTT topic and controller
  subscription.
- Job times out: confirm that the controller publishes a terminal reply with
  the exact `requestId` on a topic matching `simulation/+/replies/+`.

The agent may retry transport failures and HTTP 5xx responses with the same
`requestId`. The adapter does not deduplicate requests; the controller should
handle repeated IDs to avoid executing a retried command twice.
