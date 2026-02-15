# Adding Operation Delegation to IlmatarAAS.aasx

This guide shows how to add delegated operations to your AASX file so they can be invoked from the AAS Web UI.

## Option 1: Using REST API (Temporary - For Testing)

You can add operations programmatically via the BaSyx REST API. These will work immediately but won't persist after Docker restart.

### PowerShell Script to Add Hoist_Down Operation

```powershell
$submodelId = "aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg"

$operation = @{
    modelType = "Operation"
    idShort = "Hoist_Down"
    description = @(
        @{
            language = "en"
            text = "Lower the crane hoist"
        }
    )
    qualifiers = @(
        @{
            type = "invocationDelegation"
            value = "http://opcua-operation-service:8087/crane/hoist-down"
        }
    )
    inputVariables = @(
        @{
            value = @{
                modelType = "Property"
                idShort = "duration_ms"
                valueType = "xs:long"
                value = "10000"
                description = @(
                    @{
                        language = "en"
                        text = "Pulse duration in milliseconds (fixed at 10000)"
                    }
                )
            }
        }
    )
    outputVariables = @(
        @{
            value = @{
                modelType = "Property"
                idShort = "status"
                valueType = "xs:string"
            }
        },
        @{
            value = @{
                modelType = "Property"
                idShort = "message"
                valueType = "xs:string"
            }
        },
        @{
            value = @{
                modelType = "Property"
                idShort = "duration_ms"
                valueType = "xs:long"
            }
        }
    )
} | ConvertTo-Json -Depth 10

# Add the operation
Invoke-WebRequest `
    -Uri "http://localhost:8081/submodels/$submodelId/submodel-elements" `
    -Method Post `
    -Body $operation `
    -ContentType "application/json"
```

### Add All Crane Operations

Save this as `add-operations.ps1`:

```powershell
$submodelId = "aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg"

function Add-CraneOperation {
    param(
        [string]$IdShort,
        [string]$Description,
        [string]$DelegationUrl
    )

    $operation = @{
        modelType = "Operation"
        idShort = $IdShort
        description = @(
            @{
                language = "en"
                text = $Description
            }
        )
        qualifiers = @(
            @{
                type = "invocationDelegation"
                value = $DelegationUrl
            }
        )
        inputVariables = @(
            @{
                value = @{
                    modelType = "Property"
                    idShort = "duration_ms"
                    valueType = "xs:long"
                    value = "5000"
                    description = @(
                        @{
                            language = "en"
                            text = "Pulse duration in milliseconds"
                        }
                    )
                }
            }
        )
        outputVariables = @(
            @{
                value = @{
                    modelType = "Property"
                    idShort = "status"
                    valueType = "xs:string"
                }
            },
            @{
                value = @{
                    modelType = "Property"
                    idShort = "message"
                    valueType = "xs:string"
                }
            }
        )
    } | ConvertTo-Json -Depth 10

    Write-Host "Adding operation: $IdShort"
    try {
        $response = Invoke-WebRequest `
            -Uri "http://localhost:8081/submodels/$submodelId/submodel-elements" `
            -Method Post `
            -Body $operation `
            -ContentType "application/json"
        Write-Host "✓ $IdShort added successfully (Status: $($response.StatusCode))"
    } catch {
        Write-Host "✗ Failed to add $IdShort : $_"
    }
}

# Add all operations
Write-Host "`nAdding crane operations to AAS...`n"

Add-CraneOperation `
    -IdShort "Hoist_Down" `
    -Description "Lower the crane hoist" `
    -DelegationUrl "http://opcua-operation-service:8087/crane/hoist-down"

Add-CraneOperation `
    -IdShort "Hoist_Up" `
    -Description "Raise the crane hoist" `
    -DelegationUrl "http://opcua-operation-service:8087/crane/hoist-up"

Add-CraneOperation `
    -IdShort "Trolley_Forward" `
    -Description "Move the trolley forward" `
    -DelegationUrl "http://opcua-operation-service:8087/crane/trolley-forward"

Add-CraneOperation `
    -IdShort "Trolley_Backward" `
    -Description "Move the trolley backward" `
    -DelegationUrl "http://opcua-operation-service:8087/crane/trolley-backward"

Add-CraneOperation `
    -IdShort "Bridge_Forward" `
    -Description "Move the bridge forward" `
    -DelegationUrl "http://opcua-operation-service:8087/crane/bridge-forward"

Add-CraneOperation `
    -IdShort "Bridge_Backward" `
    -Description "Move the bridge backward" `
    -DelegationUrl "http://opcua-operation-service:8087/crane/bridge-backward"

Write-Host "`nDone! All 6 crane operations added to submodel.`n"
Write-Host "Open AAS Web UI: http://localhost:3000"
```

Run it:
```powershell
.\add-operations.ps1
```

## Option 2: Using AASX Package Explorer (Permanent)

For operations that persist across Docker restarts, edit the AASX file:

### 1. Download AASX Package Explorer
- Get it from: https://github.com/admin-shell-io/aasx-package-explorer/releases
- Or use AASX Server: https://github.com/admin-shell-io/aasx-server

### 2. Open Your AASX File
```
basyx-setup/aas/IlmatarAAS.aasx
```

### 3. Navigate to Your Submodel
- Expand the AAS tree
- Find submodel with ID: `https://example.com/ids/sm/5010_5150_1152_1102`

### 4. Add Operation

Right-click on submodel → Add SubmodelElement → Operation

**Operation Properties:**
- **idShort**: `Hoist_Down`
- **semanticId**: (optional) `https://example.com/ids/cd/CraneHoistDown`
- **description**: "Lower the crane hoist"

**Add Qualifier:**
- Click on the operation → Qualifiers → Add
- **type**: `invocationDelegation`
- **value**: `http://opcua-operation-service:8087/crane/hoist-down`

**Input Variables:**
Add OperationVariable → Property:
- **idShort**: `duration_ms`
- **valueType**: `xs:long`
- **value**: `10000`
- **description**: "Pulse duration in milliseconds (fixed at 10000)"

**Output Variables:**
1. Add OperationVariable → Property:
   - **idShort**: `status`
   - **valueType**: `xs:string`

2. Add OperationVariable → Property:
   - **idShort**: `message`
   - **valueType**: `xs:string`

3. Add OperationVariable → Property:
   - **idShort**: `duration_ms`
   - **valueType**: `xs:long`

### 5. Repeat for Other Operations
- **Hoist_Up**: `http://opcua-operation-service:8087/crane/hoist-up`
- **Trolley_Forward**: `http://opcua-operation-service:8087/crane/trolley-forward`
- **Trolley_Backward**: `http://opcua-operation-service:8087/crane/trolley-backward`
- **Bridge_Forward**: `http://opcua-operation-service:8087/crane/bridge-forward`
- **Bridge_Backward**: `http://opcua-operation-service:8087/crane/bridge-backward`
- **DriveToTarget**: `http://opcua-operation-service:8087/crane/drive-to-target`

### 6. Save and Reload

Save the AASX file and restart Docker:
```bash
docker-compose restart aas-env
```

## Testing Operations

### 1. Via AAS Web UI (Recommended)

1. Open http://localhost:3000
2. Navigate to your AAS
3. Find the submodel
4. Click on any operation (e.g., `HoistDown`)
5. (Optional) Enter duration parameter
6. Click "Execute" or "Invoke"
7. Watch OPC UA client (UaExpert) to see node change

### 2. Via REST API

```powershell
# Invoke HoistDown with default duration
$input = @(
    @{
        value = @{
            modelType = "Property"
            idShort = "duration_ms"
            value = "5000"
            valueType = "xs:long"
        }
    }
) | ConvertTo-Json -Depth 10

Invoke-WebRequest `
    -Uri "http://localhost:8081/submodels/aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg/submodel-elements/HoistDown/invoke" `
    -Method Post `
    -Body $input `
    -ContentType "application/json"
```

### 3. Direct Service Test

Test the operation service directly (bypassing BaSyx):

```powershell
Invoke-WebRequest `
    -Uri "http://localhost:8087/crane/hoist-down" `
    -Method Post `
    -ContentType "application/json"
```

## Verification Checklist

✅ OPC UA Operation Service running on port 8087  
✅ Operations added to submodel (via REST API or AASX file)  
✅ Operations have `invocationDelegation` qualifier  
✅ Qualifier value points to correct service URL  
✅ BaSyx environment restarted (if AASX was modified)  
✅ AAS Web UI shows operations as clickable/executable  
✅ OPC UA client open to monitor node changes  

## Troubleshooting

### Operation not appearing in Web UI
- Refresh the browser
- Check operation was added: `GET http://localhost:8081/submodels/{id}/submodel-elements`
- Restart BaSyx: `docker-compose restart aas-env`

### Operation invocation returns 405 (Method Not Allowed)
- This means delegation is NOT working
- Check qualifier type is exactly `"invocationDelegation"` (case-sensitive)
- Verify BaSyx feature is enabled (it's enabled by default in v2)
- Check BaSyx logs: `docker logs aas-env`

### Operation executes but nothing happens in OPC UA
- Check operation service logs: `docker logs opcua-operation-service`
- Verify service can connect to OPC UA server
- Test service directly: `POST http://localhost:8087/crane/hoist-down`
- Check OPC UA node permissions

### Can't see OPC UA value change
- The pulse is 5 seconds - watch continuously
- Increase duration parameter to 10000ms (10 seconds)
- Check your OPC UA client refresh rate
- Verify node ID is correct in service code

## Next Steps

1. ✅ Build and start the operation service: `docker-compose up -d opcua-operation-service`
2. ✅ Add operations (use PowerShell script for quick testing)
3. ✅ Test from AAS Web UI
4. ✅ Verify OPC UA changes
5. ✅ For production: Edit AASX file permanently
6. ✅ Commit changes to Git

## Comparison: Operation Delegation vs. Trigger Property

| Aspect | Trigger Property | Operation Delegation |
|--------|------------------|---------------------|
| **Pattern** | Custom polling hack | Standard AAS pattern |
| **Web UI** | Toggle switch (greyed out) | Clickable operation button |
| **Polling** | 1-second loop overhead | Event-driven (instant) |
| **Parameters** | None | Yes (duration, etc.) |
| **Return values** | None | Yes (status, message) |
| **Persistence** | Lost on Docker restart | Part of AASX (permanent) |
| **Implementation** | Simple standalone bridge | Spring Boot service |
| **Semantics** | Boolean property abuse | Proper operation invocation |

**Recommendation**: Use Operation Delegation for production. It's the proper AAS approach and is fully supported by BaSyx v2.
