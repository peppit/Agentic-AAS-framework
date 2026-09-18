# Simulation operation-delegation service

This Spring Boot service implements the device-facing side of BaSyx Operation
Delegation. It accepts delegated HTTP POST requests and publishes MQTT commands
for the OIP simulation. It does not connect to OPC UA and does not wait for
controller completion.

## Flow

```text
BaSyx AAS Operation -> HTTP POST -> this service -> MQTT -> OIP controller
```

The service runs on port `8087`. In an AAS `invocationDelegation` qualifier,
address it as `http://operation-delegation-service:8087`; from the host, use
`http://localhost:8087`.

## Endpoints and topics

| POST endpoint | Published topic | Payload |
|---|---|---|
| `/simulation/stations/{stationId}/conveyorbelt/run` | `simulation/{stationId}/operations/conveyorRunning` | `{"requestId":"...","value":true}` |
| `/simulation/stations/{stationId}/conveyorbelt/speed` | `simulation/{stationId}/operations/conveyorSpeed` | `{"requestId":"...","value":55.0}` |
| `/simulation/robots/{robotId}/movebox` | `simulation/robots/{robotId}/operations/moveBox` | Structured payload described below |
| `/simulation/stations/{stationId}/robot/move-to-home` | `simulation/{stationId}/operations/MoveToHome` | `{"requestId":"...","value":true}` |

Path values are sanitized before use as MQTT topic segments: `/`, `+`, and `#`
become `_`; a blank value becomes `unknown`.

## MoveBox

The preferred inputs are AAS Operation variables with semantic IDs:

- `urn:agent-aas:semantics:SourceTransferLocation:1`
- `urn:agent-aas:semantics:TargetTransferLocation:1`

The adapter accepts `inputArguments` or `inputVariables`; `inputVariables`
takes precedence when both are present. Variable `idShort` values are not
significant when the expected semantics are present. Missing semantic values
fall back to top-level fields, named variables, or `params` entries named
`SourcePosition` and `TargetPosition`.

See the [delegation guide](../README-OPERATION-DELEGATION.md#movebox-contract)
for the complete semantic AAS request. A direct adapter request can use:

```json
{
  "SourcePosition": "urn:agent-aas:asset-instance:conveyor01",
  "TargetPosition": "urn:agent-aas:entity:oip-factory01:pallet01",
  "requestId": "job-42",
  "runId": "experiment-7"
}
```

Published on `simulation/robots/Robot_01/operations/moveBox`:

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

The source and target are passed through unchanged. `MoveBox` does not require
or publish a `stationId`; the robot is selected by the URL and MQTT topic. If
`requestId` is absent, the service generates a UUID. If `runId` is absent, it
publishes an empty string.

## Other input formats

For conveyor and move-home calls, prefer explicit JSON objects such as
`{"running":true}`, `{"speed":55.0}`, or `{"move":true}`, with an optional
`requestId`. These endpoints also accept `value`, wrapped `inputVariables`,
arrays of wrapped variables, or primitive bodies.

The value parsers do not read `inputArguments`. An `inputArguments`-only body
causes speed to return HTTP 500 and running/move-home to publish `false`.
Within variable arrays, the parser uses the expected name first, then the first
wrapped value; an empty array defaults to `false` or speed `0.0`.

For boolean object fields and wrapped values, JSON booleans are preserved,
numbers are true when their Java integer conversion is nonzero, and trimmed
strings `true`, `1`, and `on` are true (case-insensitive). Other strings and null
become false. Thus `{"running":2}` is true. Primitive boolean bodies use literal
text matching instead: `true` and `1` are true, but `2` and the quoted JSON
string `"true"` are false. Prefer JSON booleans in named fields to avoid this
format-dependent behavior. Speed values must parse as a double.

The station path parameter is authoritative for station-addressed endpoints.

## Responses and errors

A successful response confirms MQTT publication, for example:

```json
{
  "status": "SUCCESS",
  "message": "Robot MoveBox command published",
  "requestId": "job-42",
  "runId": "experiment-7",
  "operation": "moveBox",
  "SourcePosition": "urn:agent-aas:asset-instance:conveyor01",
  "TargetPosition": "urn:agent-aas:entity:oip-factory01:pallet01"
}
```

Parsing, validation, connection, and publish failures return HTTP 500 with:

```json
{"status":"ERROR","error":"MoveBox failed: ..."}
```

Operation completion is reported separately by the controller. The Python
orchestrator expects replies carrying the same `requestId` on a topic matching
`simulation/+/replies/+`.

This service does not reserve robots, check movement, track completion, or
deduplicate `requestId` values. Direct HTTP requests bypass the orchestrator's
scheduling checks. Controller handling of repeated IDs is needed if callers
retry a command.

## Configuration

Defaults in `src/main/resources/application.yml`:

```yaml
server:
  port: 8087

simulation:
  mqtt:
    enabled: true
    broker-url: tcp://mosquitto:1883
    client-id: operation-delegation-service
    qos: 1
    station-topic-template: simulation/{stationId}/operations/{operation}
    robot-topic-template: simulation/robots/{robotId}/operations/{operation}
```

Spring properties can be overridden using normal Spring Boot configuration or
environment-variable binding.

## Build, test, and run

Use Java 17 and Maven for a local build. From this service directory:

```powershell
mvn test
mvn clean package
java -jar target/operation-delegation-service-1.0.0.jar --simulation.mqtt.broker-url=tcp://localhost:1883
```

The local run command assumes a broker is listening on host port 1883. The
default hostname `mosquitto` is for the Compose network.

From the parent `basyx-setup` directory:

```powershell
docker compose up -d --build mosquitto operation-delegation-service
docker compose logs -f operation-delegation-service
```

Direct host-side test:

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8087/simulation/robots/Robot_01/movebox" `
  -Method Post -ContentType "application/json" `
  -Body '{"SourcePosition":"urn:source","TargetPosition":"urn:target","requestId":"move-1"}'
```

For the end-to-end qualifier, allowlist, and completion-reply contracts, see
[../README-OPERATION-DELEGATION.md](../README-OPERATION-DELEGATION.md).
