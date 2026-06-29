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

To rebuild operation services after code changes:

```
docker compose up -d --build opcua-operation-service mqtt-operation-bridge
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

## OPI Simulation Delegation Flow

Current operation delegation is simulation and MQTT based:

1. AAS operation invoke in UI/API.
2. BaSyx forwards to invocationDelegation URL at opcua-operation-service.
3. opcua-operation-service publishes command to MQTT topic simulation/{stationId}/operations/{operation}.
4. Simulation listener consumes MQTT command and updates simulated state.

BaSyx delegation allowlist is configured in [basyx/aas-env.properties](basyx/aas-env.properties) for host opcua-operation-service and port 8087.

## MQTT Command Bridge

The mqtt-operation-bridge service supports MQTT-first command flow:

1. Subscribes to oip/command/conveyorbelt/+.
2. Invokes AAS operation endpoints via aas-env.
3. Publishes replies to oip/reply/conveyorbelt.

Bridge invoke URLs are configured in [docker-compose.yml](docker-compose.yml) with BRIDGE_INVOKE_CONVEYOR_RUNNING_URL and BRIDGE_INVOKE_CONVEYOR_SPEED_URL.

## Include Your Own Asset Administration Shells

To include your own AAS packages, either:

1. Put AASX files in the aas folder.
2. Upload them via AAS Web UI.

For operation delegation details, see [README-OPERATION-DELEGATION.md](README-OPERATION-DELEGATION.md).