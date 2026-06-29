# MQTT Operation Bridge

This service subscribes to MQTT command topics and invokes BaSyx operation endpoints over HTTP.

Current scope:
- Conveyor operation `Running`
- Conveyor operation `SetSpeed`

## Command Topics

- `oip/command/conveyorbelt/running`
- `oip/command/conveyorbelt/speed`

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
- `oip/reply/conveyorbelt/running`
- `oip/reply/conveyorbelt/speed`

Reply payload example:

```json
{
  "operation": "speed",
  "requestId": "cmd-2",
  "success": true,
  "message": "...HTTP response body..."
}
```

## Required Configuration

Set operation invoke URLs to your real submodel and operation idShort paths.

Environment variables:

- `BRIDGE_INVOKE_CONVEYOR_RUNNING_URL`
- `BRIDGE_INVOKE_CONVEYOR_SPEED_URL`

Example URL format:

```text
http://aas-env:8081/submodels/<base64url-submodel-id>/submodel-elements/<OperationIdShortPath>/invoke
```

## Run with Docker Compose

From `basyx-setup`:

```powershell
docker-compose up -d mqtt-operation-bridge
```
