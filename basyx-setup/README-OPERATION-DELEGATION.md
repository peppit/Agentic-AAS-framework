# BaSyx Operation Delegation for OPI Simulation

This setup uses BaSyx Operation Delegation to route operation invocations from AAS to simulation commands over MQTT.

## Purpose

When an operation is invoked in AAS Web UI (or over REST), BaSyx forwards the call to the delegated HTTP endpoint configured in the operation qualifier. In this setup, the delegated service is a Spring Boot app that publishes MQTT commands for the OPI simulation stack.

## Runtime Architecture

```text
AAS Web UI / REST
        |
        v
BaSyx AAS Environment (operation delegation)
        |
        v
opcua-operation-service (HTTP delegation target)
        |
        v
Mosquitto MQTT
        |
        v
Simulation listener (for example Python asyncua/aiomqtt)
```

In addition, there is a reverse bridge path for MQTT-first control:

```text
MQTT command topic -> mqtt-operation-bridge -> AAS operation invoke endpoint
```

## Delegation Endpoints in opcua-operation-service

Use these HTTP endpoints as operation delegation targets:

1. Conveyor running: /simulation/stations/{stationId}/conveyorbelt/run
2. Conveyor speed: /simulation/stations/{stationId}/conveyorbelt/speed
3. Robot MoveBox: /simulation/stations/{stationId}/robot/movebox
4. Robot MoveToHome: /simulation/stations/{stationId}/robot/move-to-home
5. Generic station operation: /simulation/stations/{stationId}/operation/invoke

Base URL from other containers:

http://opcua-operation-service:8087

## MQTT Topic Contract

Configured in [opcua-operation-service/src/main/resources/application.yml](opcua-operation-service/src/main/resources/application.yml):

1. Topic template: simulation/{stationId}/operations/{operation}
2. Conveyor running topic: simulation/Station_01/operations/conveyorRunning
3. Conveyor speed topic: simulation/Station_01/operations/conveyorSpeed
4. Robot MoveBox topic example: simulation/Station_01/operations/moveBox

The simulation listener must subscribe to matching topics.

## AAS Operation Qualifier Example

Example qualifier for operation delegation:

```json
{
  "type": "invocationDelegation",
        "value": "http://opcua-operation-service:8087/simulation/stations/Station_01/conveyorbelt/speed"
}
```

MoveBox qualifier example for robot0:

```json
{
        "type": "invocationDelegation",
        "value": "http://opcua-operation-service:8087/simulation/stations/Station_01/robot/movebox"
}
```

Key points:

1. Qualifier type must be exactly invocationDelegation.
2. URL must be reachable from aas-env container.
3. AAS operation inputs are forwarded and parsed by the delegated service.
4. MoveBox expects input variables Conveyor1 and Pallet1.
5. stationId is path-based in the delegation URL and should match the intended station.

## Robot MoveBox Payload Contract

For robot0 MoveBox operation delegation, define AAS input variables:

1. Conveyor1 (xs:string)
2. Pallet1 (xs:string)

Optional input variable:

1. stationId (xs:string) can still be included for downstream payload clarity, but routing is primarily path-based.

The delegated service publishes this MQTT message shape:

```json
{
        "requestId": "<generated-or-forwarded>",
        "stationId": "Station_01",
        "operation": "moveBox",
        "params": {
                "Conveyor1": "Conveyor1",
                "Pallet1": "Pallet1"
        }
}
```

Python/OPC UA listener behavior should map operation=moveBox to the waypoint sequence:

1. command=2 and execute=true (move to pick)
2. execute=false and gripper=true (grip)
3. command=3 and execute=true (move to place)
4. continue with remaining waypoints and release sequence

This keeps the operation interface stable while waypoint logic stays in the station controller.

## BaSyx Allowlist Requirement

BaSyx operation delegation target validation is enabled. Allowlist is configured in [basyx/aas-env.properties](basyx/aas-env.properties):

1. basyx.submodelrepository.feature.operation.delegation.security.allowlist.hosts=opcua-operation-service
2. basyx.submodelrepository.feature.operation.delegation.security.allowlist.ports=8087

Without this, delegation may fail with HTTP 424 and blocked private address errors.

## Quick Validation Steps

1. Start stack:

```powershell
docker compose up -d
```

2. Test delegated endpoints directly:

```powershell
Invoke-RestMethod -Uri "http://localhost:8087/simulation/stations/Station_01/conveyorbelt/run" -Method Post -ContentType "application/json" -Body '{"running":true}'
Invoke-RestMethod -Uri "http://localhost:8087/simulation/stations/Station_01/conveyorbelt/speed" -Method Post -ContentType "application/json" -Body '{"speed":55.0}'
```

3. Invoke the operation from AAS Web UI and verify published MQTT messages.

4. Inspect logs when debugging:

```powershell
docker logs -f aas-env
docker logs -f opcua-operation-service
docker logs -f mqtt-operation-bridge
docker logs -f mosquitto
```

## MQTT Operation Bridge Notes

The bridge service in [mqtt-operation-bridge/README.md](mqtt-operation-bridge/README.md) listens to OIP command topics and calls AAS operation invoke endpoints:

1. Topic filter: oip/command/conveyorbelt/+
2. Running invoke URL and Speed invoke URL are set in docker-compose environment variables.
3. Replies are published under oip/reply/conveyorbelt.

## Troubleshooting

1. HTTP 424 from AAS invoke:
   Delegation target was blocked, unreachable, or returned a downstream error. Check allowlist and service logs.

2. HTTP 404 from delegated invocation:
   Endpoint path in invocationDelegation qualifier does not match service route. Recheck URL path.

3. HTTP 200 from delegated service but simulation did not change state:
   MQTT topic contract mismatch. Confirm stationId and operation names match subscriber expectations.

4. Changes not reflected after code update:
   Rebuild service image:

```powershell
docker compose up -d --build opcua-operation-service mqtt-operation-bridge
```

## Related Files

1. [README.md](README.md)
2. [docker-compose.yml](docker-compose.yml)
3. [basyx/aas-env.properties](basyx/aas-env.properties)
4. [opcua-operation-service/src/main/resources/application.yml](opcua-operation-service/src/main/resources/application.yml)
5. [mqtt-operation-bridge/README.md](mqtt-operation-bridge/README.md)
