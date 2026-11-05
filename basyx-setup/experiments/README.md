# Robot09 runtime-onboarding experiment

This experiment measures how long a newly registered, semantically compatible
Robot09 takes to become usable by the running orchestrator.

The primary metric is:

```text
IntegrationMs = (UsableUnixUs - RegisteredUnixUs) / 1000
```

All event timestamps use Unix microseconds. `RegisteredUnixUs` is captured when
the Robot09 AAS descriptor and all seven required Submodel descriptors are
readable from the BaSyx registries. `UsableUnixUs` is captured when Robot09
passes the orchestrator's capability, reachability, state, operation-binding,
endpoint, and operation-parameter checks for `Transfer09`.

Run all commands below in PowerShell from the `basyx-setup` directory.

```powershell
Set-Location "C:\Users\Peppi\OneDrive - Aalto University\Documents\research\Agentic-AAS-framework\basyx-setup"
```

## Important experimental controls

- Keep `python-agent` running during each registration event.
- Do not restart `aas-env`. A restart would preload the local `robot09.aasx`
  and invalidate the registration boundary.
- Do not use the BoxSpawner during timing trials.
- Keep Conveyor09 `WorkpiecePresent=false`.
- Do not invoke Robot09 between repeated timing trials.
- Use **Client-side Import** in the BaSyx Web UI. The current AAS Environment
  does not provide the server-side AASX package-upload endpoint.
- Preserve the raw registration and agent logs for every trial.

The configured catalog refresh interval is five seconds. Integration latency
therefore includes the phase-dependent wait for a refresh plus catalog build
and reconciliation time.

## One-time preflight

Check the required containers:

```powershell
docker compose ps `
    aas-env `
    aas-registry `
    sm-registry `
    python-agent `
    operation-delegation-service
```

`aas-env`, `aas-registry`, and `sm-registry` should be healthy, and
`python-agent` and `operation-delegation-service` should be running.

If the timestamp instrumentation has changed, rebuild and recreate only the
agent:

```powershell
docker compose up -d --build --no-deps --force-recreate python-agent
```

Do not run `docker compose down` and do not restart `aas-env`.

Check the initial registry state:

```powershell
$aasDescriptors = @(
    (Invoke-RestMethod `
        "http://localhost:8082/shell-descriptors").result
)

$submodelDescriptors = @(
    (Invoke-RestMethod `
        "http://localhost:8083/submodel-descriptors").result
)

[pscustomobject]@{
    Conveyor09Aas = @(
        $aasDescriptors |
            Where-Object {
                $_.id -eq "urn:agent-aas:aas:conveyor09"
            }
    ).Count

    Robot09Aas = @(
        $aasDescriptors |
            Where-Object {
                $_.id -eq "urn:agent-aas:aas:robot09"
            }
    ).Count

    Robot09Submodels = @(
        $submodelDescriptors |
            Where-Object {
                $_.id -like "urn:agent-aas:robot09:*"
            }
    ).Count
}
```

The required initial result is:

```text
Conveyor09Aas    : 1
Robot09Aas       : 0
Robot09Submodels : 0
```

## Run one trial

Use a two-digit trial number in every filename. Trial 1 uses `01`, trial 2
uses `02`, and so on.

### Terminal 1: wait for complete registration

Replace `02` with the current trial number:

```powershell
py -3.13 -u .\experiments\watch_robot09_registration.py |
    Tee-Object .\experiments\robot09-registration-trial02.log
```

The watcher should initially print:

```text
Waiting for Robot09 registration...
```

If it immediately prints `resource_registered`, Robot09 is already registered;
do not continue that trial.

### Terminal 2: capture the orchestrator event

Replace `02` with the same trial number:

```powershell
docker compose logs --no-color -f --since 1s python-agent |
    Tee-Object .\experiments\robot09-agent-trial02.log
```

### Terminal 3: upload Robot09

Open the Web UI:

```powershell
Start-Process "http://localhost:3000"
```

In the Web UI:

1. Select **Client-side Import**.
2. Select `aas\robot09.aasx`.
3. Start the import once.

Terminal 1 should print a JSON `resource_registered` event and exit. Terminal
2 should subsequently print an event similar to:

```text
[ONBOARDING] {"event": "semantic_resource_usable", ...}
```

After the Robot09 onboarding event appears, stop Terminal 2 with Ctrl+C.

## Reset Robot09 before the next trial

Define these helper functions once in a PowerShell terminal. They delete only
the live Robot09 shell and its seven live Submodels. They do not delete the
local `aas\robot09.aasx` package.

```powershell
function ConvertTo-BasyxIdentifier([string]$Identifier) {
    [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes($Identifier)
    ).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Remove-BasyxResourceIfPresent([string]$Uri) {
    try {
        Invoke-WebRequest `
            -UseBasicParsing `
            -Method Delete `
            -Uri $Uri |
            Out-Null
    }
    catch {
        if (
            $_.Exception.Response.StatusCode -ne
            [Net.HttpStatusCode]::NotFound
        ) {
            throw
        }
    }
}

function Remove-LiveRobot09 {
    $robotAasId = "urn:agent-aas:aas:robot09"

    $robotSubmodelIds = @(
        "urn:agent-aas:robot09:capability-description",
        "urn:agent-aas:robot09:control-component-instance",
        "urn:agent-aas:robot09:digital-nameplate",
        "urn:agent-aas:robot09:operational-state",
        "urn:agent-aas:robot09:reachability",
        "urn:agent-aas:robot09:skill-execution",
        "urn:agent-aas:robot09:technical-data"
    )

    $aasUri = "http://localhost:8081/shells/" +
        (ConvertTo-BasyxIdentifier $robotAasId)

    Remove-BasyxResourceIfPresent $aasUri

    foreach ($submodelId in $robotSubmodelIds) {
        $submodelUri = "http://localhost:8081/submodels/" +
            (ConvertTo-BasyxIdentifier $submodelId)

        Remove-BasyxResourceIfPresent $submodelUri
    }
}

function Get-Robot09RegistryState {
    $aasDescriptors = @(
        (Invoke-RestMethod `
            "http://localhost:8082/shell-descriptors").result
    )

    $submodelDescriptors = @(
        (Invoke-RestMethod `
            "http://localhost:8083/submodel-descriptors").result
    )

    [pscustomobject]@{
        Robot09Aas = @(
            $aasDescriptors |
                Where-Object {
                    $_.id -eq "urn:agent-aas:aas:robot09"
                }
        ).Count

        Robot09Submodels = @(
            $submodelDescriptors |
                Where-Object {
                    $_.id -like "urn:agent-aas:robot09:*"
                }
        ).Count
    }
}
```

After every completed trial, reset Robot09:

```powershell
Remove-LiveRobot09
Start-Sleep -Seconds 2
Get-Robot09RegistryState
```

The result must be:

```text
Robot09Aas       : 0
Robot09Submodels : 0
```

After registry removal is confirmed, allow the continuously running
orchestrator to observe at least one absent catalog snapshot:

```powershell
Start-Sleep -Seconds 12
```

This wait occurs before the next registration and is not part of the measured
latency. It is essential: re-uploading Robot09 before the orchestrator observes
its absence may prevent a new usability-transition event.

Now repeat the three-terminal procedure with the next trial number.

## Calculate one trial

Set the two-digit trial number and run:

```powershell
$trial = "30"

$registrationFile =
    ".\experiments\robot09-registration-trial$trial.log"

$agentFile =
    ".\experiments\robot09-agent-trial$trial.log"

$registeredLine = Get-Content $registrationFile |
    Where-Object {
        $_ -match '"event":\s*"resource_registered"'
    } |
    Select-Object -Last 1

$usableLine = Get-Content $agentFile |
    Where-Object {
        $_ -match '\[ONBOARDING\]' -and
        $_ -match 'asset-instance:robot09'
    } |
    Select-Object -Last 1

if (-not $registeredLine -or -not $usableLine) {
    throw "Trial $trial does not contain both required events"
}

$registered = (
    $registeredLine.Substring(
        $registeredLine.IndexOf('{')
    ) | ConvertFrom-Json
)

$usable = (
    $usableLine.Substring(
        $usableLine.IndexOf('{')
    ) | ConvertFrom-Json
)

$result = [pscustomobject]@{
    Trial = [int]$trial

    RegisteredUnixUs = [int64](
        $registered.registered_unix_us
    )

    CatalogActivatedUnixUs = [int64](
        $usable.catalog_activated_unix_us
    )

    UsableUnixUs = [int64](
        $usable.usable_unix_us
    )

    IntegrationMs = (
        $usable.usable_unix_us -
        $registered.registered_unix_us
    ) / 1000

    RegistryToCatalogMs = (
        $usable.catalog_activated_unix_us -
        $registered.registered_unix_us
    ) / 1000

    CatalogBuildMs = [double](
        $usable.catalog_build_ms
    )

    ReconciliationMs = (
        $usable.usable_unix_us -
        $usable.catalog_activated_unix_us
    ) / 1000
}

$result | Format-List
```

The primary result is `IntegrationMs`. `CatalogBuildMs` is directly measured.
`EstimatedRefreshWaitMs` is an approximation because registration can occur
while a catalog build is already in progress.

## Aggregate all 30 trials

Use this only after all files from `trial01` through `trial30` exist:

```powershell
$results = foreach ($trialNumber in 1..30) {
    $trial = "{0:D2}" -f $trialNumber

    $registrationFile =
        ".\experiments\robot09-registration-trial$trial.log"

    $agentFile =
        ".\experiments\robot09-agent-trial$trial.log"

    if (
        -not (Test-Path $registrationFile) -or
        -not (Test-Path $agentFile)
    ) {
        Write-Warning "Missing files for trial $trial"
        continue
    }

    $registeredLine = Get-Content $registrationFile |
        Where-Object {
            $_ -match '"event":\s*"resource_registered"'
        } |
        Select-Object -Last 1

    $usableLine = Get-Content $agentFile |
        Where-Object {
            $_ -match '\[ONBOARDING\]' -and
            $_ -match 'asset-instance:robot09'
        } |
        Select-Object -Last 1

    if (-not $registeredLine -or -not $usableLine) {
        Write-Warning "Missing event in trial $trial"
        continue
    }

    $registered = (
        $registeredLine.Substring(
            $registeredLine.IndexOf('{')
        ) | ConvertFrom-Json
    )

    $usable = (
        $usableLine.Substring(
            $usableLine.IndexOf('{')
        ) | ConvertFrom-Json
    )

    [pscustomobject]@{
        Trial = $trialNumber

        RegisteredUnixUs = [int64](
            $registered.registered_unix_us
        )

        CatalogActivatedUnixUs = [int64](
            $usable.catalog_activated_unix_us
        )

        UsableUnixUs = [int64](
            $usable.usable_unix_us
        )

        IntegrationMs = (
            $usable.usable_unix_us -
            $registered.registered_unix_us
        ) / 1000

        RegistryToCatalogMs = (
            $usable.catalog_activated_unix_us -
            $registered.registered_unix_us
        ) / 1000

        CatalogBuildMs = [double](
            $usable.catalog_build_ms
        )

        EstimatedRefreshWaitMs = (
            (
                $usable.catalog_activated_unix_us -
                $registered.registered_unix_us
            ) / 1000
        ) - [double](
            $usable.catalog_build_ms
        )

        ReconciliationMs = (
            $usable.usable_unix_us -
            $usable.catalog_activated_unix_us
        ) / 1000
    }
}

if (@($results).Count -ne 30) {
    throw "Expected 30 complete trials, found $(@($results).Count)"
}

$results |
    Export-Csv `
        .\experiments\robot09-onboarding-results.csv `
        -NoTypeInformation

$results |
    Format-Table `
        Trial,
        IntegrationMs,
        RegistryToCatalogMs,
        CatalogBuildMs,
        EstimatedRefreshWaitMs,
        ReconciliationMs `
        -AutoSize
```

## Calculate summary statistics

```powershell
$values = @(
    $results.IntegrationMs |
        ForEach-Object { [double]$_ } |
        Sort-Object
)

$count = $values.Count
$mean = ($values | Measure-Object -Average).Average
$minimum = $values[0]
$maximum = $values[-1]

if ($count % 2 -eq 0) {
    $median = (
        $values[$count / 2 - 1] +
        $values[$count / 2]
    ) / 2
}
else {
    $median = $values[[math]::Floor($count / 2)]
}

$sumSquaredDifferences = 0.0

foreach ($value in $values) {
    $sumSquaredDifferences += [math]::Pow(
        $value - $mean,
        2
    )
}

$standardDeviation = [math]::Sqrt(
    $sumSquaredDifferences / ($count - 1)
)

$p95Index = [math]::Ceiling(0.95 * $count) - 1
$p95 = $values[$p95Index]

$summary = [pscustomobject]@{
    Trials = $count
    MeanMs = $mean
    MedianMs = $median
    StandardDeviationMs = $standardDeviation
    MinimumMs = $minimum
    MaximumMs = $maximum
    P95Ms = $p95
}

$summary | Format-List

$summary |
    Export-Csv `
        .\experiments\robot09-onboarding-summary.csv `
        -NoTypeInformation
```

## Optional functional validation

Do not include this validation in `IntegrationMs`. After the timing campaign is
complete, enable or manually trigger the Station09 BoxSpawner and verify that
the orchestrator selects Robot09 and invokes `ExecuteMoveBox`. This validates
operation separately from registry-to-catalog onboarding latency.

## Troubleshooting

### The registration watcher exits immediately

Robot09 is still registered. Run `Remove-LiveRobot09`, confirm both registry
counts are zero, and wait 12 seconds before retrying.

### The registration event appears but no onboarding event appears

Check the agent log:

```powershell
docker compose logs --no-color --tail 100 python-agent
```

Confirm that Robot09 has all seven Submodels and that the agent did not report
a catalog-refresh failure. Also confirm that the previous trial's removal was
visible to the orchestrator before re-uploading.

### The Web UI still shows a deleted Robot09

Refresh the browser after API deletion. Use `Get-Robot09RegistryState` as the
authoritative check; a stale UI entry is not sufficient evidence that the live
descriptor still exists.

### A trial contains a box or operation event

Discard or clearly flag that trial if the event changes Robot09's availability
state or introduces unrelated workload. The onboarding metric should be
measured with the simulation idle.
