# Agentic AAS framework: BaSyx setup

This directory contains the Docker Compose deployment for the OIP simulation.
It combines Eclipse BaSyx, semantic MQTT telemetry, an event-driven Python
orchestrator, and an HTTP-to-MQTT operation-delegation adapter.

## Runtime architecture

The primary command path is AAS-first:

```text
OIP telemetry (`oip/telemetry`)
  -> semantic MQTT-to-AAS bridge
  -> BaSyx Property update and MQTT update event
  -> Python orchestration agent
  -> discovered AAS Operation `/invoke`
  -> operation-delegation-service
  -> robot-addressed MQTT command
  -> OIP robot/controller
  -> MQTT completion or fault reply
  -> Python orchestration agent releases the robot
```

The two Python services discover AAS ownership, Submodel repository endpoints,
element paths, and semantics from the AAS and Submodel Registries. They use no
static station mapping or hard-coded BaSyx repository URLs. The legacy BaSyx
DataBridge is optional.

## Prerequisites and configuration

- Docker Desktop, or Docker Engine with the Compose plugin
- A simulation/controller that uses the MQTT contracts described below

Create the local environment file from the example:

```powershell
Copy-Item .env.example .env
```

Set `MONGO_PASSWORD`. `MEASUREMENT_RUN_ID` is optional; if it is blank, the
orchestrator generates a UUID for the run. `OPCUA_ACCESS_CODE` is retained in
the example file for historical compatibility but is not read by the current
stack.

## Start and stop

From `basyx-setup`, start the default stack:

```powershell
docker compose up -d --build
```

The default stack includes BaSyx, registries, the UI and dashboard, Mosquitto,
the semantic telemetry bridge, the delegation service, and the Python agent.

The legacy DataBridge remains available through an optional profile:

```powershell
# Run the old, statically mapped MQTT-to-AAS DataBridge
docker compose --profile legacy-databridge up -d databridge
```

Do not run `mqtt-aas-bridge` and the legacy `databridge` as writers for the
same Properties.

Stop the stack with:

```powershell
docker compose down
```

## Services

| Service | Host endpoint | Default |
|---|---|---:|
| AAS Environment | <http://localhost:8081> | Yes |
| AAS Registry | <http://localhost:8082> | Yes |
| Submodel Registry | <http://localhost:8083> | Yes |
| AAS Discovery | <http://localhost:8084> | Yes |
| Dashboard API | <http://localhost:8085> | Yes |
| Operation Delegation Service | <http://localhost:8087> | Yes |
| AAS Web UI | <http://localhost:3000> | Yes |
| Mosquitto | `localhost:1883` | Yes |
| MQTT-to-AAS telemetry bridge | Background worker | Yes |
| Python orchestration agent | Background worker | Yes |
| BaSyx DataBridge | Background worker | `legacy-databridge` only |

## Semantic telemetry contract

OIP publishes QoS 1 JSON messages to `oip/telemetry`:

```json
{
  "assetId": "urn:agent-aas:asset-instance:conveyor01",
  "semanticId": "urn:agent-aas:semantics:WorkpiecePresent:1",
  "value": true,
  "eventId": "conveyor01-workpiece-42"
}
```

- `assetId` must equal an instance AAS descriptor's `globalAssetId`.
- `semanticId` must identify exactly one `Property` owned by that asset.
- `value` is coerced according to the discovered AAS `valueType`.
- `eventId` is optional and enables per-asset duplicate suppression. `sequence`
  is accepted as an alternative. Other fields, including timestamps, are
  ignored.

The bridge recursively discovers nested Properties and patches the selected
Property's `$value` endpoint. It refreshes its complete routing catalog every
`REGISTRY_REFRESH_SECONDS`; a route miss also causes an immediate refresh.
Rejected messages and permanent update failures are reported on
`oip/fault/telemetry-bridge`.

See [mqtt-aas-bridge/README.md](mqtt-aas-bridge/README.md) for the full bridge
contract.

## Orchestration logic

On startup and every `REGISTRY_REFRESH_SECONDS`, the Python agent builds an
atomic `SemanticCatalog` from Registry descriptors and fetched Submodels. A
failed refresh leaves the last complete snapshot active.

For each false-to-true semantic trigger transition, the agent:

1. Resolves `(trigger globalAssetId, trigger semanticId)` to one or more
   `ProcessRequirement` elements.
2. Reads the required capability semantic plus canonical transfer source and
   target identities from that requirement.
3. Finds instance resources offering that semantic capability through an IDTA
   `CapabilityRealizedBy` relationship to a Skill.
4. Rejects Skills that cannot reach both locations, have `Disabled=true`, or
   lack a discovered Operation with semantic source and target inputs.
5. Rejects resources unless `AvailableForScheduling=true`; resources with
   `FaultActive=true` or `IsMoving=true` are also rejected.
6. Selects the lexicographically stable first runnable resource and reserves it
   atomically inside the single agent process.
7. Invokes the Operation at its Registry-advertised Submodel endpoint, passing
   canonical source/target values and `requestId`/`runId` metadata.
8. Correlates controller replies by `requestId`, records metrics, and releases
   the resource on completion, failure, timeout, or shutdown.

A true trigger is latched. It must become false before another true update can
create a new job. Jobs that cannot be matched or reserved fail immediately;
they are not queued for a later retry. HTTP invocation retries apply only to
transport errors and HTTP 5xx responses.

The agent subscribes to:

- BaSyx update events:
  `sm-repository/+/submodels/+/submodelElements/+/updated`
- Controller replies: `simulation/+/replies/+`

Accepted terminal reply bodies include:

```json
{"requestId":"<job-id>","status":"completed"}
```

`success: true|false` may be used instead of `status`. Status values
`started`, `running`, and `accepted` are non-terminal. Failures may put details
in `error` or `message`.

## AAS modeling contract

The current orchestration path is driven by model semantics, not `idShort`
names. A compatible setup needs:

- an instance AAS with a non-empty `globalAssetId`;
- Properties carrying the state semantics used by the agent;
- an offered IDTA Capability carrying its domain semantic;
- `CapabilityRealizedBy` from that Capability to an IDTA Skill;
- `CanReach` relationships from the Skill to both transfer locations;
- `SkillInvokedByOperation` from the Skill to an Operation;
- Operation inputs with
  `urn:agent-aas:semantics:SourceTransferLocation:1` and
  `urn:agent-aas:semantics:TargetTransferLocation:1`; and
- a Process Requirements Submodel containing a Transfer Requirement with
  Trigger Resource, Trigger Condition, Required Capability, Transfer Source,
  and Transfer Target references.

Type AASs are discovered but are not treated as schedulable instances or
telemetry destinations.

To onboard another robot, register its AAS and Submodels with the required
semantic relationships and state. After the next refresh it is eligible
without editing Python code or restarting the agent. Look for
`semantic resource added` in:

```powershell
docker compose logs -f python-agent
```

## Operation and controller boundary

The primary transport operation is `MoveBox`. Its delegation URL is
robot-addressed:

```text
http://operation-delegation-service:8087/simulation/robots/{robotId}/movebox
```

The adapter publishes to:

```text
simulation/robots/{robotId}/operations/moveBox
```

The MQTT payload contains canonical `SourcePosition` and `TargetPosition`
values. It deliberately contains no `stationId`, and the adapter performs no
identity translation. The OIP controller must therefore accept or resolve the
canonical identities.

The bundled Robot 01 `MoveBox` qualifier matches this endpoint. Some bundled
non-primary conveyor and move-home qualifiers still use older resource-based
paths that the adapter does not expose; these limitations are listed in the
delegation guide.

See [README-OPERATION-DELEGATION.md](README-OPERATION-DELEGATION.md) and
[operation-delegation-service/README.md](operation-delegation-service/README.md)
for endpoint and payload details.

## Key environment variables

The Compose file supplies the main defaults. Useful overrides include:

| Component | Variables |
|---|---|
| Python agent | `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC`, `OPERATION_REPLY_TOPIC`, `AAS_REGISTRY_URL`, `SUBMODEL_REGISTRY_URL`, `REGISTRY_REFRESH_SECONDS`, `SEMANTIC_DISCOVERY_DIAGNOSTIC`, `HTTP_TIMEOUT_SECONDS`, `OPERATION_TIMEOUT_SECONDS`, `INVOKE_RETRY_COUNT`, `ORCHESTRATOR_LOG_CSV_PATH`, `MEASUREMENT_RUN_ID` |
| Telemetry bridge | `MQTT_HOST`, `MQTT_PORT`, `MQTT_TELEMETRY_TOPIC`, `AAS_REGISTRY_URL`, `SUBMODEL_REGISTRY_URL`, `REGISTRY_REFRESH_SECONDS`, `HTTP_TIMEOUT_SECONDS`, `AAS_UPDATE_RETRY_COUNT`, `AAS_RETRY_BASE_SECONDS`, `MQTT_RECONNECT_SECONDS`, `ASSET_QUEUE_SIZE`, `EVENT_DEDUP_WINDOW`, `FAULT_TOPIC` |

`REGISTRY_REFRESH_SECONDS <= 0` disables periodic refresh in the Python agent.
The telemetry bridge clamps its refresh interval to at least 0.1 seconds.

Orchestration outcomes are appended to `../orchestrator_logs.csv`. If that file
has an older header, the agent preserves it and writes the current schema to a
`.phase2.csv` sibling.

## AAS packages and legacy telemetry

Files placed in `aas/` are preloaded by the AAS Environment. Packages can also
be uploaded through the Web UI.

The static DataBridge configuration under `databridge/` consumes the old
station-specific simulation topics and writes fixed AAS paths. See
[databridge/README.md](databridge/README.md).

## Verification

Run the unit tests without starting Compose:

```powershell
python -m unittest discover -s python-agent -p "test_*.py"
python -m unittest discover -s mqtt-aas-bridge -p "test_*.py"
mvn -f operation-delegation-service/pom.xml test
```

Inspect runtime logs with:

```powershell
docker compose logs -f mqtt-aas-bridge python-agent operation-delegation-service
```
