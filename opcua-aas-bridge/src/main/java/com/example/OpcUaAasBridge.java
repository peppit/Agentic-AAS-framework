package com.example;

import org.eclipse.milo.opcua.sdk.client.OpcUaClient;
import org.eclipse.milo.opcua.sdk.client.api.subscriptions.UaMonitoredItem;
import org.eclipse.milo.opcua.sdk.client.api.subscriptions.UaSubscription;
import org.eclipse.milo.opcua.stack.core.AttributeId;
import org.eclipse.milo.opcua.stack.core.types.builtin.DataValue;
import org.eclipse.milo.opcua.stack.core.types.builtin.NodeId;
import org.eclipse.milo.opcua.stack.core.types.builtin.Variant;
import org.eclipse.milo.opcua.stack.core.types.builtin.unsigned.UInteger;
import org.eclipse.milo.opcua.stack.core.types.enumerated.MonitoringMode;
import org.eclipse.milo.opcua.stack.core.types.enumerated.TimestampsToReturn;
import org.eclipse.milo.opcua.stack.core.types.structured.MonitoredItemCreateRequest;
import org.eclipse.milo.opcua.stack.core.types.structured.MonitoringParameters;
import org.eclipse.milo.opcua.stack.core.types.structured.ReadValueId;
import org.eclipse.milo.opcua.stack.core.types.builtin.StatusCode;

import okhttp3.*;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonArray;

import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class OpcUaAasBridge {
    
    // OPC UA Configuration
    private static final String OPC_UA_ENDPOINT = "opc.tcp://localhost:4840";
    private static final String OPC_UA_HOIST_SPEED_NODE = "ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Speed";
    private static final String OPC_UA_HOIST_DOWN_NODE = "ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Down";
    
    // AAS Configuration
    // Base64 encoded submodel ID: aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg
    private static final String SUBMODEL_ID = "aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg";
    private static final String PROPERTY_ID_SHORT = "Hoist_Speed";
    private static final String OPERATION_ID_SHORT = "Hoist_Down";
    private static final String AAS_ENDPOINT = "http://localhost:8081";
    
    private static final OkHttpClient httpClient = new OkHttpClient();
    private static OpcUaClient opcUaClient;

    public static void main(String[] args) throws Exception {
        System.out.println("Starting Bidirectional OPC UA <-> AAS Bridge...");
        
        // Create OPC UA client
        opcUaClient = OpcUaClient.create(OPC_UA_ENDPOINT);
        opcUaClient.connect().get();
        System.out.println("Connected to OPC UA server: " + OPC_UA_ENDPOINT);

        // Start OPC UA -> AAS monitoring (Hoist Speed)
        startOpcUaToAasMonitoring();
        
        // Start AAS -> OPC UA monitoring (Hoist_Down operation)
        startAasToOpcUaMonitoring();

        System.out.println("Bridge is running in bidirectional mode. Press Ctrl+C to stop.");

        // Keep the application running
        CompletableFuture<Void> future = new CompletableFuture<>();
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            try {
                opcUaClient.disconnect().get();
                System.out.println("Disconnected from OPC UA server");
            } catch (Exception e) {
                e.printStackTrace();
            }
            future.complete(null);
        }));
        
        future.get();
    }

    /**
     * Monitor OPC UA node changes and update AAS property
     */
    private static void startOpcUaToAasMonitoring() throws Exception {
        // Create subscription
        UaSubscription subscription = opcUaClient.getSubscriptionManager()
            .createSubscription(1000.0).get();
        System.out.println("Created OPC UA subscription");

        // Create monitored item for the hoist speed node
        ReadValueId readValueId = new ReadValueId(
            NodeId.parse(OPC_UA_HOIST_SPEED_NODE),
            AttributeId.Value.uid(),
            null,
            null
        );

        MonitoringParameters parameters = new MonitoringParameters(
            UInteger.valueOf(1),  // client handle
            1000.0,               // sampling interval
            null,                 // filter
            UInteger.valueOf(10), // queue size
            true                  // discard oldest
        );

        MonitoredItemCreateRequest request = new MonitoredItemCreateRequest(
            readValueId,
            MonitoringMode.Reporting,
            parameters
        );

        // Subscribe to value changes
        subscription.createMonitoredItems(
            TimestampsToReturn.Both,
            List.of(request),
            (item, id) -> item.setValueConsumer((monitoredItem, value) -> {
                try {
                    updateAasProperty(value);
                } catch (Exception e) {
                    System.err.println("Error updating AAS: " + e.getMessage());
                }
            })
        ).get();

        System.out.println("✓ OPC UA -> AAS: Monitoring " + OPC_UA_HOIST_SPEED_NODE + " -> Hoist_Speed");
    }

    /**
     * Poll AAS for operation invocations and write to OPC UA
     * Using a trigger property approach: Monitor Hoist_Down_Trigger property
     * When it's set to "true", write to OPC UA and reset it to "false"
     */
    private static void startAasToOpcUaMonitoring() {
        ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(1);
        
        scheduler.scheduleAtFixedRate(() -> {
            try {
                checkHoistDownTrigger();
            } catch (Exception e) {
                System.err.println("Error checking AAS trigger: " + e.getMessage());
            }
        }, 0, 1, TimeUnit.SECONDS); // Poll every 1 second
        
        System.out.println("✓ AAS -> OPC UA: Polling for Hoist_Down_Trigger property");
    }

    /**
     * Check if Hoist_Down_Trigger property is set to true
     * If yes, write to OPC UA and reset the trigger
     */
    private static void checkHoistDownTrigger() throws Exception {
        // Read the trigger property value
        String url = String.format("%s/submodels/%s/submodel-elements/Hoist_Down_Trigger/$value",
            AAS_ENDPOINT, SUBMODEL_ID);

        Request request = new Request.Builder()
            .url(url)
            .get()
            .build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                // Property might not exist yet - that's okay
                return;
            }

            String value = response.body().string().replace("\"", "").trim();
            
            if ("true".equalsIgnoreCase(value)) {
                System.out.println("==============================================");
                System.out.println("AAS operation triggered: Hoist_Down");
                System.out.println("==============================================");
                
                try {
                    // Write TRUE to OPC UA
                    writeOpcUaBoolean(OPC_UA_HOIST_DOWN_NODE, true);
                    
                    // Reset the trigger back to false
                    resetHoistDownTrigger();
                    
                    // Schedule writing FALSE after 5 seconds in a separate thread
                    // This gives you time to see the value change in your OPC UA client
                    new Thread(() -> {
                        try {
                            Thread.sleep(5000); // 5 seconds instead of 500ms
                            writeOpcUaBoolean(OPC_UA_HOIST_DOWN_NODE, false);
                            System.out.println("==============================================");
                        } catch (Exception e) {
                            System.err.println("Error writing false to OPC UA: " + e.getMessage());
                            e.printStackTrace();
                        }
                    }).start();
                    
                } catch (Exception e) {
                    System.err.println("ERROR during Hoist_Down operation:");
                    e.printStackTrace();
                }
            }
        } catch (Exception e) {
            // Don't crash the polling thread on errors
            System.err.println("Error in checkHoistDownTrigger: " + e.getMessage());
        }
    }

    /**
     * Reset the Hoist_Down_Trigger property back to false
     */
    private static void resetHoistDownTrigger() throws Exception {
        String url = String.format("%s/submodels/%s/submodel-elements/Hoist_Down_Trigger/$value",
            AAS_ENDPOINT, SUBMODEL_ID);

        RequestBody body = RequestBody.create(
            "\"false\"",
            MediaType.parse("application/json")
        );

        Request request = new Request.Builder()
            .url(url)
            .patch(body)
            .build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                System.out.println("  ✓ Reset Hoist_Down_Trigger to false");
            }
        }
    }

    /**
     * Update AAS property when OPC UA value changes
     */
    private static void updateAasProperty(DataValue value) throws Exception {
        Object opcValue = value.getValue().getValue();
        System.out.println("OPC UA value changed: " + opcValue);

        // Build AAS property value update URL for BaSyx v2
        String url = String.format("%s/submodels/%s/submodel-elements/%s/$value",
            AAS_ENDPOINT, SUBMODEL_ID, PROPERTY_ID_SHORT);

        // Just send the raw value as JSON string
        String jsonValue = String.format("\"%s\"", opcValue.toString());

        RequestBody body = RequestBody.create(
            jsonValue,
            MediaType.parse("application/json")
        );

        Request request = new Request.Builder()
            .url(url)
            .patch(body)
            .build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                System.out.println("  ✓ Updated AAS Hoist_Speed = " + opcValue);
            } else {
                System.err.println("  ✗ Failed to update AAS. Status: " + response.code());
            }
        }
    }

    /**
     * Write boolean value to OPC UA node
     */
    private static void writeOpcUaBoolean(String nodeId, boolean value) throws Exception {
        NodeId node = NodeId.parse(nodeId);
        DataValue dataValue = new DataValue(new Variant(value));
        
        System.out.println("  → Attempting to write to OPC UA " + nodeId + " = " + value);
        StatusCode status = opcUaClient.writeValue(node, dataValue).get();
        
        if (status.isGood()) {
            System.out.println("  ✓ Write successful! Status: " + status);
            
            // Read back the value to verify it was written
            DataValue readBack = opcUaClient.readValue(0, TimestampsToReturn.Neither, node).get();
            Object readValue = readBack.getValue().getValue();
            System.out.println("  → Read back value: " + readValue + " (should be " + value + ")");
        } else {
            System.err.println("  ✗ Failed to write to OPC UA. Status: " + status);
            System.err.println("  → Status code: " + status.getValue());
        }
    }

    /**
     * Invoke AAS operation (synchronous)
     */
    private static JsonObject invokeAasOperation(String operationIdShort, JsonObject inputVariables) throws Exception {
        String url = String.format("%s/submodels/%s/submodel-elements/%s/invoke",
            AAS_ENDPOINT, SUBMODEL_ID, operationIdShort);

        RequestBody body = RequestBody.create(
            inputVariables.toString(),
            MediaType.parse("application/json")
        );

        Request request = new Request.Builder()
            .url(url)
            .post(body)
            .build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                String responseBody = response.body().string();
                return JsonParser.parseString(responseBody).getAsJsonObject();
            } else {
                throw new Exception("Operation invocation failed: " + response.code());
            }
        }
    }
}
