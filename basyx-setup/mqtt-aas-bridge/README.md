# Semantic MQTT-to-AAS telemetry bridge

This service is the default telemetry data plane. It has no station or asset
binding file. It discovers AAS ownership, Submodel endpoints, Property paths,
and value types from the AAS and Submodel Registries.

## Telemetry contract

Publish QoS 1 messages to `oip/telemetry`:

```json
{
  "assetId": "urn:agent-aas:asset-instance:conveyor01",
  "semanticId": "urn:agent-aas:semantics:WorkpiecePresent:1",
  "value": true,
  "eventId": "conveyor01-workpiece-42",
  "timestamp": "2026-08-28T12:00:00Z"
}
```

`assetId` is the AAS descriptor's `globalAssetId`. `semanticId` identifies a
Property belonging to that asset. The bridge looks up the exact Registry-
advertised Submodel endpoint and recursively discovered `idShort` path, coerces
the value to the Property's AAS `valueType`, and patches its `$value` endpoint.

The contract intentionally contains no station name, simulator node name,
Submodel ID, Property `idShort`, or signal alias. Changing `Sensor_BoxPresent`
to `RenamedSensor`, for example, requires no bridge change while its semantic ID
remains stable.

## Dynamic discovery

The route catalog is rebuilt every `REGISTRY_REFRESH_SECONDS` and atomically
replaced. A newly registered asset becomes routable without a bridge restart;
an unregistered asset disappears on the next refresh. A route miss also causes
an immediate refresh to close the onboarding race.

Only instance AASs and `Property` elements are routable. If one asset declares
the same semantic ID on multiple Properties, that ambiguous route is rejected
and reported instead of guessing.

Assets are processed concurrently, while each asset has a bounded FIFO queue.
Optional `eventId` or `sequence` fields provide QoS-1 duplicate suppression.
AAS writes use exponential retry. Rejected or permanently failed messages are
published to `oip/fault/telemetry-bridge` with their canonical identities.

Key environment variables:

- `AAS_REGISTRY_URL`, `SUBMODEL_REGISTRY_URL`, `REGISTRY_REFRESH_SECONDS`
- `MQTT_HOST`, `MQTT_PORT`, `MQTT_TELEMETRY_TOPIC`
- `HTTP_TIMEOUT_SECONDS`, `AAS_UPDATE_RETRY_COUNT`
- `ASSET_QUEUE_SIZE`, `EVENT_DEDUP_WINDOW`, `FAULT_TOPIC`

Start the gateway:

```powershell
docker compose up -d --build mqtt-aas-bridge
```

The legacy BaSyx DataBridge remains available only through the
`legacy-databridge` profile for comparison. Do not run both writers because
they update the same AAS Properties.
