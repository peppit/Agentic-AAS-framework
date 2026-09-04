# Legacy BaSyx MQTT DataBridge

This directory contains the old, statically mapped MQTT-to-AAS telemetry
configuration. It is retained for comparison and compatibility; the default
runtime uses `mqtt-aas-bridge`, which discovers routes from AAS semantics and
Registry descriptors.

This configuration does not use OPC UA.

## Enable the legacy bridge

From the parent `basyx-setup` directory:

```powershell
docker compose --profile legacy-databridge up -d databridge
docker compose logs -f databridge
```

The container receives the files in this directory at `/usr/share/config`.
Mosquitto and the AAS Environment are started as dependencies.

Do not run this bridge and `mqtt-aas-bridge` as writers for the same
Properties, because both will update AAS state independently.

## Current static mappings

`mqttconsumer.json` defines eight station-specific input topics:

| Station | Input topic | Transformer input field | AAS destination |
|---|---|---|---|
| `Station_01` | `simulation/Station_01/isRunning` | `isRunning` | `IsRunning` |
| `Station_01` | `simulation/Station_01/currentSpeed` | `currentSpeed` | `CurrentSpeed` |
| `Station_01` | `simulation/Station_01/boxDetected` | `boxDetected` | `Sensor_BoxPresent` |
| `Station_01` | `simulation/Station_01/isMoving` | `isMoving` | `IsMoving` |
| `Station_02` | `simulation/Station_02/isRunning` | `isRunning` | `IsRunning` |
| `Station_02` | `simulation/Station_02/currentSpeed` | `currentSpeed` | `CurrentSpeed` |
| `Station_02` | `simulation/Station_02/boxDetected` | `boxDetected` | `Sensor_BoxPresent` |
| `Station_02` | `simulation/Station_02/isMoving` | `isMoving` | `IsMoving` |

The destinations in `aasserver.json` contain fixed Base64URL Submodel IDs and
fixed `idShortPath` values. Consequently, renaming or moving a Property,
changing a Submodel ID, or adding a station requires configuration changes and
a DataBridge restart.

## Configuration files

- `mqttconsumer.json`: MQTT broker, topic, and source IDs
- `jsonatatransformer.json`: source-to-JSONata transformer definitions
- `jsonExtract*.jsonata`: extract and coerce each old simulator payload
- `aasserver.json`: fixed BaSyx Submodel endpoints and Property paths
- `routes.json`: connects each source, transformer, and AAS sink
- `context.properties`: DataBridge HTTP context (`localhost:4001` inside the
  container)

The JSONata expressions accept either a primitive or an object containing the
expected signal field. Boolean-like strings are normalized; speed is converted
to a number.

## Change a mapping

1. Add or edit the MQTT source in `mqttconsumer.json`.
2. Add or edit its JSONata transformer and expression.
3. Add the exact Submodel endpoint and `idShortPath` in `aasserver.json`.
4. Connect those IDs in `routes.json` with `"trigger": "event"`.
5. Restart the profiled service:

```powershell
docker compose --profile legacy-databridge restart databridge
```

For new integrations, prefer the semantic bridge documented in
[../mqtt-aas-bridge/README.md](../mqtt-aas-bridge/README.md). Its input is the
single canonical `oip/telemetry` contract and it needs no static station or
Submodel mapping.
