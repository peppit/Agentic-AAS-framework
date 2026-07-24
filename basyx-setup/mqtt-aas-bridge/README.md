# Plug-and-play MQTT-to-AAS gateway

This service is the default telemetry data plane. The legacy BaSyx DataBridge is
kept behind the `legacy-databridge` Compose profile for comparison.

## Discovery

A station announces itself with a retained message:

```text
factory/{stationId}/manifest
```

The manifest declares its telemetry signals, value types, target AAS submodels,
and target element `idShort` values. The gateway validates the manifest, creates
a bounded FIFO queue for that station, and starts processing immediately. No
gateway restart or copied JSONata route is required.

The top-level `stations.json` is the canonical registry. The simulation server
converts each active registry entry into a retained manifest. The same registry
is mounted as the gateway's bootstrap binding file, allowing stations to work
before their retained manifests have been received.

## Telemetry

The preferred topic contract is:

```text
factory/{stationId}/telemetry/{signal}
```

Payload:

```json
{
  "value": true,
  "eventId": "station_01-boxDetected-42",
  "timestamp": "2026-07-23T12:00:00Z"
}
```

For migration, the gateway also accepts the simulator's existing topics:

```text
simulation/{stationId}/{signal}
```

Messages are validated and converted according to the manifest. Different
stations are processed concurrently, while each station's FIFO queue preserves
local ordering. Optional `eventId` or `sequence` values provide QoS-1 duplicate
suppression.

## Reliability

- Bounded station queues provide backpressure.
- AAS writes use exponential retry.
- Invalid or permanently failed messages are published to
  `oip/fault/telemetry-bridge/{stationId}`.
- Retained manifests allow the gateway to reconstruct discovery state after a
  restart.

Start the default gateway:

```powershell
docker compose up -d --build mqtt-aas-bridge
```

Run the legacy DataBridge only for comparison:

```powershell
docker compose --profile legacy-databridge up -d databridge
```

Do not run both writers simultaneously because they update the same AAS
properties.
