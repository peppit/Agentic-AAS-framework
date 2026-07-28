# BaSyx DataBridge - Crane OPC UA to AAS Integration

## Overview
This DataBridge connects your crane's OPC UA server to your BaSyx AAS Web UI, enabling:
- Real-time crane data → AAS properties
- Future: AAS operations → Crane control commands

## Your Configuration

### OPC UA Server
- **Endpoint**: `opc.tcp://localhost:4840`
- **Security**: None (for development)
- **Update Interval**: 1000ms (1 second)

### AAS Infrastructure
- **Submodel Endpoint**: `http://localhost:8081/submodels`
- **Registry**: `http://localhost:8082/shell-descriptors`
- **Web UI**: `http://localhost:3000`

## Setup Steps

### 1. Find Your Submodel Information

Open AAS Web UI at `http://localhost:3000`:
1. Navigate to your crane AAS
2. Open the submodel you want to update (e.g., "CraneStatus", "SensorData")
3. Copy the **Submodel ID** (full URI, usually starts with `https://` or `http://`)
4. Note the **idShort** of the property to update (e.g., "HookHeight", "LoadWeight")

### 2. Configure OPC UA Node Mapping

Edit `opcuaToAas.json` and replace:
- `YOUR_SUBMODEL_ID_HERE` → Your submodel ID from step 1
- `YOUR_PROPERTY_IDSHORT_HERE` → Your property idShort from step 1

**Example:**
```json
{
  "submodelid": "https://konecranes.com/crane/submodels/status",
  "idShortPath": "HookHeight",
  "value": $
}
```

### 3. Configure OPC UA Node to Monitor

Edit `opcuaconsumer.json` and set the `nodeInformation` field:

**For a single node:**
```json
{
  "uniqueId": "craneOpcUa",
  "serverUrl": "opc.tcp://localhost:4840",
  "nodeInformation": "ns=2;s=Crane.HookHeight",
  ...
}
```

**OPC UA Node ID Formats:**
- String: `ns=2;s=Crane.HookHeight`
- Integer: `ns=3;i=1001`
- GUID: `ns=2;g=09087e75-8e5e-499b-954f-f2a9603db28a`

Use an OPC UA client like **UaExpert** to browse your server and find node IDs.

### 4. For Multiple Crane Properties

To map multiple OPC UA nodes to different AAS properties:

**A. Add another consumer** in `opcuaconsumer.json`:
```json
[
  {
    "uniqueId": "craneHookHeight",
    "serverUrl": "opc.tcp://localhost:4840",
    "nodeInformation": "ns=2;s=Crane.HookHeight",
    ...
  },
  {
    "uniqueId": "craneLoadWeight",
    "serverUrl": "opc.tcp://localhost:4840",
    "nodeInformation": "ns=2;s=Crane.LoadWeight",
    ...
  }
]
```

**B. Add transformer** in `jsonatatransformer.json`:
```json
[
  {
    "uniqueId": "hookHeightTransformer",
    "queryPath": "hookHeight.json",
    "inputType": "JsonString",
    "outputType": "JsonString"
  },
  {
    "uniqueId": "loadWeightTransformer",
    "queryPath": "loadWeight.json",
    "inputType": "JsonString",
    "outputType": "JsonString"
  }
]
```

**C. Create JSONata files** (`hookHeight.json`, `loadWeight.json`):
```json
{
  "submodelid": "https://konecranes.com/crane/submodels/status",
  "idShortPath": "HookHeight",
  "value": $
}
```

**D. Add routes** in `routes.json`:
```json
[
  {
    "routeId": "hookHeightRoute",
    "datasource": "craneHookHeight",
    "transformers": ["jsonUnmarshaller", "hookHeightTransformer"],
    "datasinks": ["aasServer"],
    "trigger": "event"
  },
  {
    "routeId": "loadWeightRoute",
    "datasource": "craneLoadWeight",
    "transformers": ["jsonUnmarshaller", "loadWeightTransformer"],
    "datasinks": ["aasServer"],
    "trigger": "event"
  }
]
```

## Start DataBridge

```powershell
cd c:\Users\tarkkap2\Documents\Research\workspace2\basyx-setup
docker compose --profile legacy-databridge up -d databridge
```

## Monitor Operation

```powershell
# Check if running
docker ps | Select-String databridge

# View live logs
docker logs -f databridge

# Restart after config changes
docker-compose restart databridge
```

## Troubleshooting

### DataBridge won't start
```powershell
docker logs databridge
```
- Check JSON syntax in config files
- Verify OPC UA server is running on port 4840
- Ensure submodel ID is correct

### Data not updating in AAS
1. Check DataBridge logs for errors
2. Verify OPC UA node ID is correct
3. Test OPC UA connection with UaExpert
4. Confirm submodel and property exist in AAS

### "Connection refused" errors
- OPC UA server must be accessible from Docker
- Using `network_mode: host` allows DataBridge to reach `localhost:4840`
- If OPC UA is on another machine, change to `opc.tcp://MACHINE_IP:4840`

## Network Mode Note

This configuration uses `network_mode: host` because:
- Your OPC UA server runs on `localhost:4840`
- Your AAS services run on `localhost:8081-8085`
- Host network mode allows DataBridge to access all localhost services

If your OPC UA server is on a different machine, you can:
1. Remove `network_mode: host`
2. Add `ports: - '8090:8090'`
3. Change OPC UA URL to the actual IP address

## Next Steps

Once data flows from OPC UA → AAS:
1. Verify updates in AAS Web UI
2. Set up bi-directional control (AAS operations → OPC UA methods)
3. Add more crane properties
4. Implement crane control operations
