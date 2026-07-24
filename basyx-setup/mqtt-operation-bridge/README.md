# MQTT Operation Bridge

This service subscribes to MQTT command topics and invokes BaSyx operation endpoints over HTTP.

Current scope:
- Conveyor operations `Running`, `Speed`
- Robot operations `MoveBox`, `MoveToHome`

## Command Topics

Preferred dynamic pattern:

- `oip/command/{stationId}/{action}`

Examples:

- `oip/command/station_01/running`
- `oip/command/station_01/speed`
- `oip/command/station_01/moveBox`
- `oip/command/station_01/moveToHome`

## Accepted Payloads

### Running

```json
{"requestId":"cmd-1","value":true}
```

Also accepted: `true`, `false`, `1`, `0`, `on`.

### Speed

```json
{"requestId":"cmd-2","value":55.0}
```

Also accepted: `55.0`.

## Reply Topics

Replies are published to:

- `oip/reply/{stationId}/{operation}`

Reply payload example:

```json
{
  "operation": "speed",
  "requestId": "cmd-2",
  "success": true,
  "message": "...HTTP response body..."
}
```

## Dynamic Routing Configuration

Use station bindings so the bridge can construct invoke URLs dynamically at runtime.

Environment variables:

- `BRIDGE_MQTT_TOPIC_FILTER=oip/command/+/+`
- `BRIDGE_MQTT_COMMAND_PREFIX=oip/command`
- `BRIDGE_AAS_BASE_URL=http://aas-env:8081`
- `STATION_REGISTRY_FILE=/config/stations.json`
- `BRIDGE_STATION_BINDINGS=station_01=<conveyorOperationsSubmodelB64>|<robotSkillsSubmodelB64>` (optional inline fallback)

The default Compose setup mounts the shared top-level `stations.json`. Relevant
fields are:

```json
{
  "schemaVersion": "1.0",
  "stations": {
    "station_03": {
      "stationId": "Station_03",
      "conveyorOperationsSubmodelB64": "<operations-submodel>",
      "robotSkillsSubmodelB64": "<skills-submodel>"
    }
  }
}
```

Runtime invoke URL pattern:

```text
{BRIDGE_AAS_BASE_URL}/submodels/{submodelB64}/submodel-elements/{OperationIdShortPath}/invoke
```

If a station binding is missing, the bridge falls back to static URLs (if provided):

- `BRIDGE_INVOKE_CONVEYOR_RUNNING_URL`
- `BRIDGE_INVOKE_CONVEYOR_SPEED_URL`
- `BRIDGE_INVOKE_ROBOT_MOVE_BOX_URL`
- `BRIDGE_INVOKE_ROBOT_MOVE_TO_HOME_URL`

## Run with Docker Compose

From `basyx-setup`:

```powershell
docker-compose up -d mqtt-operation-bridge
```
