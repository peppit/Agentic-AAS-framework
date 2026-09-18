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

`eventId` is optional. If that key is absent, `sequence` is used instead.
A non-null ID is converted to a string for per-asset duplicate suppression.
Use an ID unique across all signals for that asset. Other fields, including
`timestamp`, are ignored.

## Route discovery

The bridge:

1. Lists AAS descriptors from the AAS Registry.
2. Obtains each AAS's Submodel descriptors from inline descriptors, then the
   per-shell Registry endpoint, then the repository AAS model's Submodel
   references if the per-shell response is empty.
3. Uses matching standalone Submodel Registry descriptors as the authority for
   repository endpoints.
4. Fetches the Submodels and recursively indexes `Property` elements by
   `(globalAssetId, semanticId)`.
5. PATCHes the selected Registry-advertised
   `{submodelEndpoint}/submodel-elements/{idShortPath}/$value` endpoint.

Only instance AASs and `Property` elements are routable. Type AASs are skipped.
If one asset exposes the same semantic ID on multiple Properties, that route is
ambiguous and is excluded rather than guessed.

The route catalog is rebuilt and atomically swapped every
`REGISTRY_REFRESH_SECONDS`; a route miss also triggers an immediate refresh.
If discovery raises an exception, the prior catalog is retained. Individual
AAS discovery or Submodel fetch failures are instead logged and skipped, so a
refresh can replace the previous catalog with fewer routes. Check discovery
warnings when previously working telemetry loses its route.

## Value handling, ordering, and faults

The bridge converts `value` according to the discovered AAS `valueType`:

| Type | Conversion |
|---|---|
| Boolean | JSON booleans or trimmed, case-insensitive `"true"`/`"false"` strings; numeric values are rejected |
| Integer types | Python `int()` conversion; booleans are rejected, fractional numbers are truncated, and signed/unsigned ranges are not checked |
| Float, double, decimal | Python `float()` conversion; booleans are rejected |
| String, URI, date/time | Python `str()` conversion; URI and date/time formats are not validated |

Unsupported types and failed conversions produce faults. This is conversion,
not full XML Schema validation: for example, `1.9` becomes integer `1` and
`-1` passes the local `unsignedInt` conversion. The AAS server may reject the
result. PATCH bodies contain the converted value as a JSON string.

Each asset has a bounded FIFO queue, preserving order within that asset while
allowing different assets to update concurrently. When the queue is full,
consumption waits, delaying intake for other assets too. Duplicate tracking
retains the most recent `EVENT_DEDUP_WINDOW` IDs for each active asset.
IDs are remembered before the AAS write succeeds, so resending a failed event
with the same remembered ID is suppressed. Queues and duplicate history are
in memory and cleared on disconnect or shutdown; queued updates are not replayed
by the bridge after reconnecting.

AAS PATCH requests retry HTTP errors, including 4xx responses, with exponential
backoff. Invalid input, missing or ambiguous routes, and permanent update
failures are published with QoS 1 to
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

These are process environment settings. For Compose, configure the service's
`environment` block or an override file; adding a key to `.env` alone does not
pass it into the container. Use a positive queue size for bounded buffering.

## Run and test

From the parent `basyx-setup` directory:

```powershell
docker compose up -d --build mqtt-aas-bridge
docker compose logs -f mqtt-aas-bridge
```

For local tests, use Python 3.13 to match the container image. In a virtual
environment, install `mqtt-aas-bridge/requirements.txt`, then run:

```powershell
python -m unittest discover -s mqtt-aas-bridge -p "test_*.py"
```

The legacy BaSyx DataBridge is available through the `legacy-databridge`
profile, but its destinations need remapping for the current bundled models.
See [the legacy bridge guide](../databridge/README.md). Do not run both bridges
as writers for the same AAS Properties.
