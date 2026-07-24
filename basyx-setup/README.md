# BaSyx Setup for OPI Simulation

This folder contains the Docker-based BaSyx setup used for OPI Simulation with MQTT-based operation delegation.

Prerequisite: Docker Desktop (or Docker Engine with Compose plugin) is installed.

## Configuration

Secrets are not stored in source control. Before starting the stack, create a .env file in this folder:

```
copy .env.example .env
```

Then edit .env and set values:

| Variable | Description |
|---|---|
| `MONGO_PASSWORD` | Password for the MongoDB `mongoAdmin` user |
| `OPCUA_ACCESS_CODE` | Legacy variable from earlier OPC UA flow. It is not used by the current simulation MQTT delegation path. |

The .env file is excluded from git via .gitignore.

## Start the Stack

1. Clone or extract the repository on your device.
2. Create and populate .env as described above.
3. Open a terminal and navigate to the folder.
4. Start all services:

```
docker compose up -d
```

## Available Services

- AAS Environment: [http://localhost:8081](http://localhost:8081)
- AAS Registry: [http://localhost:8082](http://localhost:8082)
- Submodel Registry: [http://localhost:8083](http://localhost:8083)
- AAS Discovery: [http://localhost:8084](http://localhost:8084)
- AAS Web UI: [http://localhost:3000](http://localhost:3000)
- Dashboard API: [http://localhost:8085](http://localhost:8085)
- Operation Delegation Service: [http://localhost:8087](http://localhost:8087)
- MQTT Operation Bridge: [http://localhost:8091](http://localhost:8091)
- Mosquitto MQTT Broker: localhost:1883
- Python Agent: background worker (no public HTTP port)

## OPI Simulation Delegation Flow

Current operation delegation is simulation and MQTT based:

1. AAS operation invoke in UI/API.
2. BaSyx forwards to invocationDelegation URL at opcua-operation-service.
3. opcua-operation-service publishes command to MQTT topic simulation/{stationId}/operations/{operation}.
4. Simulation listener consumes MQTT command and updates simulated state.

BaSyx delegation allowlist is configured in [basyx/aas-env.properties](basyx/aas-env.properties) for host opcua-operation-service and port 8087.

## MQTT Command Bridge

The mqtt-operation-bridge service supports MQTT-first command flow:

1. Subscribes to `oip/command/+/+`.
2. Invokes AAS operation endpoints via aas-env.
3. Publishes replies to `oip/reply/{stationId}/{operation}`.

The bridge resolves station-specific operation submodels from
[stations.json](stations.json).

## Python Agent (Event-Driven Robot Orchestration)

The python-agent listens to AAS submodel update events and dispatches robot operations dynamically from robot capability metadata.

Current behavior:

1. Subscribes to AAS update events and correlated operation replies on `simulation/+/replies/+`.
2. Processes boolean sensor properties whose idShort contains Present or Clear.
3. Enqueues a job on valid detection and routes it to a robot by matching TriggerSensor -> TargetOperation in SupportedCapabilities.
4. Latches runtime state by station and correlates commands with their `requestId`.
5. Rearms a station only after its operation reports `completed` and its sensor reports `false`.
6. Polls `IsMoving` as a diagnostic/compatibility monitor.

Key python-agent environment variables (see [docker-compose.yml](docker-compose.yml)):

1. BASYX_BASE_URL
2. MQTT_HOST / MQTT_PORT / MQTT_TOPIC / OPERATION_REPLY_TOPIC
3. STATION_REGISTRY_FILE
4. JOB_TIMEOUT_SECONDS / INVOKE_RETRY_COUNT

The registry variable points to the shared `stations.json` file in the default
Compose setup.

## Add a Station

[stations.json](stations.json) is the canonical runtime registry used by the
simulation manifest publisher, telemetry bridge, MQTT operation bridge, and
orchestrator. Station identifiers are explicit and may use any name; there is
no positional or `station_01`/`station_02` inference.

To add a station:

1. Add one entry below `stations` with a unique `stationId` and the station's
   conveyor telemetry, conveyor operations, robot state, and robot skills
   submodel IDs.
2. Add or upload the corresponding conveyor AASX. Add a robot AASX only when the
   station introduces a new robot.
3. Add a `SupportedCapabilities` route for the station to a robot skills
   submodel.
4. Add the station to the OIP scene and map its OPC UA tags.
5. Restart `server.py` and the services that cache registry data:
   `python-agent` and `mqtt-operation-bridge`.

When `STATION_IDS` is unset, `server.py` creates every station declared in the
registry. Set `STATION_IDS` to a comma-separated subset to run only selected
stations.

## Include Your Own Asset Administration Shells

To include your own AAS packages, either:

1. Put AASX files in the aas folder.
2. Upload them via AAS Web UI.

For operation delegation details, see [README-OPERATION-DELEGATION.md](README-OPERATION-DELEGATION.md).
