# Semantic MQTT-to-AAS telemetry bridge

This Python service is the default telemetry data plane. It consumes canonical
OIP telemetry and updates AAS Properties. It has no station/asset mapping file
and no hard-coded Submodel IDs or `idShort` paths.

## Input contract

Publish QoS 1 JSON messages to `oip/telemetry`:

```json
{
  "assetId": "urn:agent-aas:asset-instance:conveyor01",
  "semanticId": "urn:agent-aas:semantics:WorkpiecePresent:1",
  "value": true,
  "eventId": "conveyor01-workpiece-42",
  "timestamp": "2026-08-28T12:00:00Z"
}
```

Required fields:

- `assetId`: the instance AAS descriptor's exact `globalAssetId`
- `semanticId`: the semantic ID of one Property owned by that asset
- `value`: the value to write

`eventId` is optional. `sequence` is accepted as its alternative. Either value
is converted to a string and used for per-asset duplicate suppression. Other
fields, including `timestamp`, are ignored.

## Route discovery

The bridge:

1. Lists AAS descriptors from the AAS Registry.
2. Obtains each AAS's Submodel descriptors from inline descriptors, the
   per-shell Registry endpoint, or—if that endpoint returns no entries—the
   Submodel references in the repository's AAS model.
3. Uses matching standalone Submodel Registry descriptors as the authority for
   repository endpoints.
4. Fetches the Submodels and recursively indexes `Property` elements by
   `(globalAssetId, semanticId)`.
5. PATCHes the selected Registry-advertised
   `{submodelEndpoint}/submodel-elements/{idShortPath}/$value` endpoint.

Only instance AASs and `Property` elements are routable. Type AASs are skipped.
If one asset exposes the same semantic ID on multiple Properties, that route is
ambiguous and is excluded rather than guessed.

The complete route catalog is rebuilt and atomically swapped every
`REGISTRY_REFRESH_SECONDS`. A route miss triggers an immediate refresh, which
closes the race when an asset has just been registered. A failed refresh keeps
the prior catalog.

## Value handling, ordering, and faults

The bridge coerces `value` to the discovered AAS `valueType`. Supported types
are booleans, common integer types, float/double/decimal, string/URI, and basic
date/time strings. Invalid or unsupported values fail instead of being guessed.

Each asset has a bounded FIFO queue, preserving order within that asset while
allowing different assets to update concurrently. When the queue is full,
consumption applies backpressure. Duplicate tracking retains the most recent
`EVENT_DEDUP_WINDOW` IDs for each active asset.

AAS PATCH requests use exponential-backoff retries. Invalid input, missing or
ambiguous routes, and permanent update failures are published with QoS 1 to
`oip/fault/telemetry-bridge`:

```json
{
  "error": "...",
  "assetId": "urn:agent-aas:asset-instance:conveyor01",
  "semanticId": "urn:agent-aas:semantics:WorkpiecePresent:1",
  "eventId": "conveyor01-workpiece-42"
}
```

Identity fields are included when the input was parsed far enough to recover
them.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MQTT_HOST` | `mosquitto` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_TELEMETRY_TOPIC` | `oip/telemetry` | Input topic |
| `AAS_REGISTRY_URL` | `http://aas-registry:8080` | AAS Registry base URL |
| `SUBMODEL_REGISTRY_URL` | `http://sm-registry:8080` | Submodel Registry base URL |
| `REGISTRY_REFRESH_SECONDS` | `5` | Catalog refresh interval; clamped to at least 0.1 s |
| `HTTP_TIMEOUT_SECONDS` | `8` | Registry and repository HTTP timeout |
| `AAS_UPDATE_RETRY_COUNT` | `5` | Maximum PATCH attempts |
| `AAS_RETRY_BASE_SECONDS` | `0.2` | First retry delay |
| `MQTT_RECONNECT_SECONDS` | `2` | Delay after an MQTT connection error |
| `FAULT_TOPIC` | `oip/fault/telemetry-bridge` | Rejected/permanent-failure topic |
| `ASSET_QUEUE_SIZE` | `1000` | Per-asset queue capacity |
| `EVENT_DEDUP_WINDOW` | `4096` | Remembered event IDs per active asset |

## Run and test

From the parent `basyx-setup` directory:

```powershell
docker compose up -d --build mqtt-aas-bridge
docker compose logs -f mqtt-aas-bridge
python -m unittest discover -s mqtt-aas-bridge -p "test_*.py"
```

The legacy BaSyx DataBridge is available through the `legacy-databridge`
profile. Do not run both bridges as writers for the same AAS Properties.
