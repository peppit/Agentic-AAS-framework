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

The `ExecuteMoveBox` qualifier in `aas/robot01.aasx` uses the supported
robot-addressed endpoint and is the operation used by the primary path. The
other bundled qualifiers are not currently aligned with this adapter:

- conveyor qualifiers use `/simulation/conveyors/{conveyorId}/...`, while the
  adapter currently exposes station-addressed conveyor endpoints;
- the robot move-home qualifier uses
  `/simulation/robots/{robotId}/move-to-home`, while the adapter currently
  exposes the station-addressed endpoint; and
- the bundled `convey-workpiece` qualifier has no corresponding controller
  endpoint.

Those non-primary AAS Operations will return HTTP 404 until either their AASX
qualifiers or the adapter routes are aligned.

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
and adds `requestId` and `runId` metadata inputs. The adapter also accepts
legacy inputs named `SourcePosition` and `TargetPosition` when semantic IDs are
not present.

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

Conveyor running accepts `running`, `value`, an AAS variable, or a primitive
boolean-like value. Conveyor speed similarly accepts `speed`, `value`, an AAS
variable, or a number. Move-to-home accepts `move`, `value`, an AAS variable,
or a boolean-like value. The station always comes from the endpoint path for
these operations.

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

- Non-terminal: `started`, `running`, `accepted`
- Successful terminal: `completed`, `complete`, `succeeded`, `success`
- Failed terminal: `failed`, `fault`, `faulted`, `error`

On a terminal reply or timeout, the agent records the result and releases the
reserved resource. Unknown request IDs and unsupported statuses are ignored.

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
docker compose up -d --build operation-delegation-service
docker compose logs -f aas-env operation-delegation-service mosquitto python-agent
```

- HTTP 404: the qualifier path does not match one of the endpoint paths above.
- HTTP 424 from BaSyx: inspect the allowlist, delegation service availability,
  and the downstream HTTP response.
- HTTP 200 but no simulated action: inspect the MQTT topic and controller
  subscription.
- Job times out: confirm that the controller publishes a terminal reply with
  the exact `requestId` on a topic matching `simulation/+/replies/+`.
