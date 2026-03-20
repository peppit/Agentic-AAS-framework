# OPC UA Operation Delegation Service

Spring Boot service that implements BaSyx Operation Delegation pattern to bridge AAS operations to OPC UA crane controls.

## Overview

This service acts as the **delegated operation handler** for BaSyx AAS operations. When an operation is invoked in the AAS Web UI or via API, BaSyx forwards the request to this service via HTTP, which then executes the corresponding OPC UA write operation.

## Architecture

```
AAS Web UI → BaSyx Environment → Operation Delegation → This Service → OPC UA Server
```

### How It Works

1. **AAS Operation Definition**: Operations in the AASX file have a `Qualifier` with:
   - `type`: `"invocationDelegation"`
   - `value`: `"http://opcua-operation-service:8087/crane/hoist-down"` (service URL)

2. **BaSyx Delegation**: When operation is invoked, BaSyx checks for the delegation qualifier and forwards the request

3. **This Service**: Receives the HTTP POST request and translates it to OPC UA write operations

4. **OPC UA**: Writes boolean pulse (true → 10 seconds → false) to crane control nodes

## Supported Operations

| Operation | Endpoint | OPC UA Node |
|-----------|----------|-------------|
| Hoist_Down | `/crane/hoist-down` | `ns=7;s=DX_Custom_V.Controls.Hoist.Down` |
| Hoist_Up | `/crane/hoist-up` | `ns=7;s=DX_Custom_V.Controls.Hoist.Up` |
| Trolley_Forward | `/crane/trolley-forward` | `ns=7;s=DX_Custom_V.Controls.Trolley.Forward` |
| Trolley_Backward | `/crane/trolley-backward` | `ns=7;s=DX_Custom_V.Controls.Trolley.Backward` |
| Bridge_Forward | `/crane/bridge-forward` | `ns=7;s=DX_Custom_V.Controls.Bridge.Forward` |
| Bridge_Backward | `/crane/bridge-backward` | `ns=7;s=DX_Custom_V.Controls.Bridge.Backward` |
| DriveToTarget | `/crane/drive-to-target` | `Target.Bridge`, `Target.Trolley`, `Target.Hoist`, `DriveToTarget.Execute` |

## Configuration

Edit `src/main/resources/application.yml`:

```yaml
opcua:
  endpoint: opc.tcp://host.docker.internal:4840  # OPC UA server address
```

**Note**: `host.docker.internal` is used to access the host machine from inside Docker.

## Building

### Standalone JAR
```bash
mvn clean package
java -jar target/opcua-operation-service-1.0.0.jar
```

### Docker Image
```bash
docker build -t opcua-operation-service:1.0.0 .
```

## Running

### With Docker Compose (Recommended)
The service is already configured in the main `docker-compose.yml`:

```yaml
opcua-operation-service:
  build:
    context: ./opcua-operation-service
  container_name: opcua-operation-service
  ports:
    - '8087:8087'
  restart: always
```

Start with:
```bash
cd ../  # Go to basyx-setup root
docker-compose up -d opcua-operation-service
```

### Standalone
```bash
mvn spring-boot:run
```

## API Examples

### Invoke Hoist_Down (default 10s duration)
```bash
curl -X POST http://localhost:8087/crane/hoist-down \
  -H "Content-Type: application/json"
```

### Invoke DriveToTarget with parameters
```bash
curl -X POST http://localhost:8087/crane/drive-to-target \
  -H "Content-Type: application/json" \
  -d '[
    {"value":{"idShort":"Bridge","value":"200.0"}},
    {"value":{"idShort":"Trolley","value":"50.0"}},
    {"value":{"idShort":"Hoist","value":"12.5"}}
  ]'
```

### Expected Response
```json
{
  "status": "SUCCESS",
  "message": "HoistDown executed successfully",
  "duration_ms": 10000
}
```

## Integration with BaSyx

To enable operation delegation in your AASX file, add operations with delegation qualifiers. Example JSON (to add via REST API or AASX editor):

```json
{
  "modelType": "Operation",
  "idShort": "Hoist_Down",
  "description": [
    {
      "language": "en",
      "text": "Lower the crane hoist"
    }
  ],
  "qualifiers": [
    {
      "type": "invocationDelegation",
      "value": "http://opcua-operation-service:8087/crane/hoist-down"
    }
  ],
  "inputVariables": [
    {
      "value": {
        "modelType": "Property",
        "idShort": "duration_ms",
        "valueType": "xs:long",
        "description": [{"language": "en", "text": "Pulse duration in milliseconds (fixed at 10000)"}]
      }
    }
  ],
  "outputVariables": [
    {
      "value": {
        "modelType": "Property",
        "idShort": "status",
        "valueType": "xs:string"
      }
    },
    {
      "value": {
        "modelType": "Property",
        "idShort": "message",
        "valueType": "xs:string"
      }
    }
  ]
}
```

## Advantages Over Trigger Property Approach

✅ **Standard AAS Pattern**: Uses proper Operation elements  
✅ **Event-Driven**: No polling overhead  
✅ **Cleaner API**: Standard operation invocation via Web UI  
✅ **Parameters**: Can pass duration and other parameters  
✅ **Return Values**: Get execution status and results  
✅ **Web UI Support**: Operations show as clickable buttons  

## Troubleshooting

### Service can't connect to OPC UA
- Check OPC UA server is running on host
- Verify `host.docker.internal` resolves (Windows/Mac Docker Desktop feature)
- For Linux, use `--add-host=host.docker.internal:host-gateway` in docker run

### Operation not delegated
- Check BaSyx feature is enabled (enabled by default)
- Verify qualifier type is exactly `"invocationDelegation"`
- Check service URL is accessible from BaSyx container
- A 404 from this service shows up as a 424 in BaSyx
- Review BaSyx logs for delegation errors

### Pulse not visible in OPC UA
- Increase `PULSE_DURATION_MS` in code
- Check OPC UA client refresh rate
- Verify node permissions allow writes

## Development

### Adding New Operations

1. Add OPC UA node constant
2. Create POST endpoint in `CraneOperationController`
3. Implement OPC UA write logic
4. Add operation to AASX with delegation qualifier
5. Test via Web UI or REST API

### Logging

Check logs:
```bash
docker logs opcua-operation-service
```

## Next Steps

1. Update your AASX file to include operations with `invocationDelegation` qualifiers
2. Restart BaSyx environment to load updated AASX
3. Test operations from AAS Web UI
4. Monitor OPC UA changes in UaExpert
