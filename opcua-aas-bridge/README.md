# OPC UA to AAS Bridge

A Java application that bridges OPC UA data to BaSyx AAS (Asset Administration Shell) environment, enabling **bidirectional** real-time synchronization of industrial control data.

## ✅ Status: FULLY WORKING

**Both directions operational and verified!**
- ✅ **OPC UA → AAS**: Real-time monitoring and property updates
- ✅ **AAS → OPC UA**: Trigger-based control via property polling

## Features

- ✅ **Real-time OPC UA Monitoring**: Connects to OPC UA server and subscribes to node value changes
- ✅ **Automatic AAS Updates**: Updates corresponding AAS property values via BaSyx v2 API
- ✅ **Bidirectional Control**: Poll AAS trigger properties and write to OPC UA nodes
- ✅ **Pulse Generation**: Creates timed pulses on OPC UA boolean nodes (5-second duration)
- ✅ **Write Verification**: Read-back confirmation of all OPC UA write operations
- ✅ **BaSyx v2 Compatible**: Uses correct `/$value` endpoint with PATCH method
- ✅ **Continuous Operation**: Runs as background service until manually stopped
- 📊 **Live Synchronization**: Real-time crane control system integration

## Current Configuration

The bridge implements **bidirectional** communication:

### Direction 1: OPC UA → AAS (Real-time Monitoring)
- **OPC UA Node**: `ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Speed` (Crane hoist speed)
- **AAS Property**: `Hoist_Speed` in submodel `aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg`
- **Update Method**: Subscription-based (1000ms sampling interval)

### Direction 2: AAS → OPC UA (Trigger-based Control)
- **AAS Trigger Property**: `Hoist_Down_Trigger` (boolean property in same submodel)
- **OPC UA Target Node**: `ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Down` (Crane hoist down control)
- **Update Method**: Polling every 1 second
- **Pulse Behavior**: When trigger = `true`, writes `true` to OPC UA for 5 seconds, then `false`

### Endpoints

- OPC UA Server: `opc.tcp://localhost:4840`
- BaSyx AAS Environment: `http://localhost:8081`
- BaSyx Web UI: `http://localhost:8082` (check updated values here!)

## Configuration

To monitor different OPC UA nodes or AAS properties, edit the constants in `OpcUaAasBridge.java`:

```java
private static final String OPC_UA_ENDPOINT = "opc.tcp://localhost:4840";
private static final String OPC_UA_NODE_ID = "ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Speed";
private static final String SUBMODEL_ID = "aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg";
private static final String PROPERTY_ID_SHORT = "Hoist_Speed";
private static final String AAS_ENDPOINT = "http://localhost:8081";
```

## Build

```bash
mvn clean package
```

This creates a fat JAR with all dependencies at `target/opcua-aas-bridge-1.0-SNAPSHOT.jar`

## Run

### Windows (PowerShell)
```powershell
& "C:\Program Files\Eclipse Adoptium\jdk-25.0.1.8-hotspot\bin\java.exe" -jar target\opcua-aas-bridge-1.0-SNAPSHOT.jar
```

### Linux/Mac
```bash
java -jar target/opcua-aas-bridge-1.0-SNAPSHOT.jar
```

### Expected Output
```
Starting Bidirectional OPC UA <-> AAS Bridge...
Connected to OPC UA server: opc.tcp://localhost:4840
Created OPC UA subscription
Monitoring OPC UA -> AAS: ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Speed -> Hoist_Speed
Monitoring AAS -> OPC UA: Hoist_Down_Trigger -> ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Down
Bridge is running. Press Ctrl+C to stop.

OPC UA value changed: 20.0
Successfully updated AAS property Hoist_Speed: 20.0

AAS operation triggered: Hoist_Down
Writing to OPC UA: ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Down = true
Write successful! Status: StatusCode{name=Good, value=0x00000000, quality=good}
Read back value: true (should be true)
[5 seconds later]
Writing to OPC UA: ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Down = false
Write successful! Status: StatusCode{name=Good, value=0x00000000, quality=good}
Read back value: false (should be false)
```

## How It Works

### Direction 1: OPC UA → AAS (Monitoring)

1. **OPC UA Connection**: Establishes secure connection to OPC UA server using Eclipse Milo SDK
2. **Subscription Setup**: Creates monitored item subscription with 1000ms sampling interval
3. **Value Change Detection**: Listens for value change notifications from OPC UA server
4. **AAS Update**: When value changes, sends PATCH request to BaSyx v2 API endpoint:
   ```
   PATCH /submodels/{submodelId}/submodel-elements/{idShort}/$value
   Content-Type: application/json
   Body: "20.0"
   ```

### Direction 2: AAS → OPC UA (Control)

1. **Trigger Polling**: Every 1 second, polls the `Hoist_Down_Trigger` property value
2. **Trigger Detection**: When property value = `"true"`, initiates pulse sequence
3. **OPC UA Write**: Writes `true` to target OPC UA boolean node
4. **Write Verification**: Reads back the node value to confirm write succeeded
5. **Trigger Reset**: Updates AAS property back to `"false"` to prevent re-triggering
6. **Delayed Reset**: After 5 seconds, writes `false` to OPC UA node (completes pulse)
7. **Continuous Operation**: Polling continues in background until stopped

## Technical Stack

- **Java 17+** (tested with JDK 25)
- **Eclipse Milo 0.6.8** - OPC UA client library
- **OkHttp 4.11.0** - HTTP client for REST API calls
- **Gson 2.10.1** - JSON serialization
- **Maven 3.9+** - Build tool with Shade plugin

## Requirements

### Runtime
- Java 17 or higher (Java 25 recommended)
- OPC UA server accessible at configured endpoint
- BaSyx AAS environment v2.0-SNAPSHOT running

### BaSyx Environment
The bridge requires a running BaSyx environment. Use the docker-compose setup in `../basyx-setup`:

```bash
cd ../basyx-setup
docker-compose up -d
```

This starts:
- AAS Environment (port 8081)
- AAS Registry (port 8082)
- Submodel Registry (port 8083)
- AAS Web UI (port 8085)
- MongoDB (backend storage)

## Verified Functionality

✅ **Tested on**: December 15, 2025  
✅ **BaSyx Version**: 2.0.0-SNAPSHOT  
✅ **OPC UA Server**: Custom crane control server  
✅ **Data Flow**: 
- OPC UA → Bridge → BaSyx AAS → Web UI ✅
- AAS Web UI → Bridge → OPC UA nodes ✅
✅ **API Compatibility**: BaSyx v2 REST API `/$value` endpoint with PATCH  
✅ **Write Verification**: All OPC UA writes confirmed with read-back  
✅ **Pulse Duration**: 5-second pulse visible in OPC UA client (UaExpert tested)

## Troubleshooting

### Bridge won't connect to OPC UA
- Verify OPC UA server is running: Check `opc.tcp://localhost:4840`
- Check firewall settings
- Verify node ID exists in OPC UA server

### AAS updates fail (404 error)
- Ensure BaSyx AAS environment is running
- Verify submodel ID is correct (Base64 encoded)
- Check that property `Hoist_Speed` exists in the submodel
- Restart AAS container to reload AASX file if property disappeared:
  ```bash
  docker-compose restart aas-env
  ```

### Trigger property disappeared after Docker restart
**This is expected behavior!** BaSyx reloads from the AASX file on startup. The `Hoist_Down_Trigger` property was created programmatically via REST API and is not in the original AASX file.

**Temporary Solution** (recreate property after each Docker restart):
```powershell
$body = @"
{
  "modelType": "Property",
  "idShort": "Hoist_Down_Trigger",
  "value": "false",
  "valueType": "xs:boolean"
}
"@

Invoke-WebRequest -Uri "http://localhost:8081/submodels/aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg/submodel-elements" -Method Post -Body $body -ContentType "application/json"
```

**Permanent Solution**: See "Next Steps" section below.

### Property disappeared from Web UI
- BaSyx stores data in memory by default
- Restart AAS environment container to reload from AASX file
- Check logs: `docker logs aas-env`

## How to Test

### Test 1: OPC UA → AAS (Monitoring)

1. Start the bridge (see "Run" section above)
2. Open BaSyx Web UI: `http://localhost:8085`
3. Navigate to your AAS and find the `Hoist_Speed` property
4. Change the OPC UA node value in your OPC UA client (e.g., UaExpert)
5. Watch the `Hoist_Speed` property update in real-time in the Web UI
6. Check bridge console for confirmation logs

### Test 2: AAS → OPC UA (Control)

1. Ensure bridge is running
2. Open your OPC UA client (e.g., UaExpert) and navigate to node: `ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Down`
3. Trigger the operation via REST API (PowerShell):
   ```powershell
   Invoke-WebRequest -Uri "http://localhost:8081/submodels/aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg/submodel-elements/Hoist_Down_Trigger/$value" -Method Patch -Body '"true"' -ContentType "application/json"
   ```
4. **Immediately** check OPC UA client - you should see `Hoist.Down = true`
5. Wait 5 seconds - the value should change back to `false`
6. Check bridge console for detailed write verification logs

**Note**: The AAS Web UI toggle for `Hoist_Down_Trigger` may be greyed out. Use the REST API command above to trigger instead.

## Next Steps

### 1. Add Trigger Property to AASX File (Recommended for Production)

To make the `Hoist_Down_Trigger` property persist across Docker restarts, you need to add it to the AASX file:

**Option A: Using AASX Package Explorer**
1. Download [AASX Package Explorer](https://github.com/admin-shell-io/aasx-package-explorer)
2. Open your AASX file: `basyx-setup/aas/IlmatarAAS.aasx`
3. Navigate to the submodel: `https://example.com/ids/sm/5010_5150_1152_1102`
4. Add a new Property:
   - **idShort**: `Hoist_Down_Trigger`
   - **valueType**: `xs:boolean`
   - **value**: `false`
5. Save the AASX file
6. Restart Docker containers to reload: `docker-compose restart`

**Option B: Manual XML Editing**
1. Extract the AASX file (it's a ZIP archive)
2. Edit the XML file containing your submodel definition
3. Add the property element within the appropriate submodel
4. Repackage as AASX
5. Restart Docker containers

### 2. Additional Trigger Properties

Add more trigger properties for other crane operations:
- `Hoist_Up_Trigger` → `ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Up`
- `Trolley_Left_Trigger` → corresponding OPC UA node
- `Trolley_Right_Trigger` → corresponding OPC UA node

Update the bridge code to poll multiple trigger properties.

### 3. MQTT-Based Real-Time Events (Advanced)

Instead of polling, use BaSyx's MQTT integration (Mosquitto on port 1883):
- Subscribe to AAS property change events
- React immediately when trigger property changes
- More efficient than 1-second polling
- Requires configuration of BaSyx MQTT integration

### 4. Configuration File Support

Move hardcoded values to external config file (JSON/YAML):
```json
{
  "opcua": {
    "endpoint": "opc.tcp://localhost:4840",
    "nodes": {
      "hoist_speed": "ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Speed",
      "hoist_down": "ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Down"
    }
  },
  "basyx": {
    "endpoint": "http://localhost:8081",
    "submodel_id": "aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg"
  },
  "mappings": [
    {
      "direction": "opcua_to_aas",
      "opcua_node": "hoist_speed",
      "aas_property": "Hoist_Speed"
    },
    {
      "direction": "aas_to_opcua",
      "aas_trigger": "Hoist_Down_Trigger",
      "opcua_node": "hoist_down",
      "pulse_duration_ms": 5000
    }
  ]
}
```

### 5. Docker Containerization

Package the bridge as a Docker container for easier deployment:
- Create `Dockerfile` with JDK base image
- Include compiled JAR
- Add to `docker-compose.yml`
- Run alongside BaSyx services

## Future Enhancements

- 🔄 ~~Bidirectional communication (AAS operations → OPC UA write)~~ ✅ **DONE**
- 📝 Configuration file support (JSON/YAML instead of hardcoded constants)
- 🔌 Multiple node mapping support
- 📊 Metrics and monitoring dashboard
- 🐳 Docker containerization
- 🔐 Security: OPC UA authentication and encryption
- 🔔 MQTT integration for real-time event handling

## License

This project uses Eclipse Milo (EPL-1.0) and other open-source libraries.
