package com.example;

import org.eclipse.milo.opcua.sdk.client.OpcUaClient;
import org.eclipse.milo.opcua.sdk.client.api.subscriptions.UaSubscription;
import org.eclipse.milo.opcua.stack.core.AttributeId;
import org.eclipse.milo.opcua.stack.core.types.builtin.DataValue;
import org.eclipse.milo.opcua.stack.core.types.builtin.NodeId;
import org.eclipse.milo.opcua.stack.core.types.builtin.unsigned.UInteger;
import org.eclipse.milo.opcua.stack.core.types.enumerated.MonitoringMode;
import org.eclipse.milo.opcua.stack.core.types.enumerated.TimestampsToReturn;
import org.eclipse.milo.opcua.stack.core.types.structured.MonitoredItemCreateRequest;
import org.eclipse.milo.opcua.stack.core.types.structured.MonitoringParameters;
import org.eclipse.milo.opcua.stack.core.types.structured.ReadValueId;


import okhttp3.*;

import java.util.List;
import java.util.concurrent.CompletableFuture;


public class OpcUaAasBridge {
    
    // OPC UA Configuration - use environment variable with fallback to localhost
    // For Docker: set to opc.tcp://host.docker.internal:4840
    // For local: uses localhost:4840
    private static final String OPC_UA_ENDPOINT = System.getenv().getOrDefault(
        "OPCUA_ENDPOINT", "opc.tcp://localhost:4840");
    private static final String OPC_UA_HOIST_SPEED_NODE = "ns=7;s=SCF.PLC.DX_Custom_V.Controls.Hoist.Speed";
    private static final String OPC_UA_TROLLEY_SPEED_NODE = "ns=7;s=SCF.PLC.DX_Custom_V.Controls.Trolley.Speed";
    private static final String OPC_UA_BRIDGE_SPEED_NODE = "ns=7;s=SCF.PLC.DX_Custom_V.Controls.Bridge.Speed";

    
    // AAS Configuration - use environment variable with fallback
    // For Docker: set to http://aas-env:8081 (container network)
    // For local: uses localhost:8081
    // Base64 encoded submodel ID: aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg
    private static final String SUBMODEL_ID = "aHR0cHM6Ly9leGFtcGxlLmNvbS9pZHMvc20vNTAxMF81MTUwXzExNTJfMTEwMg";
    private static final String AAS_ENDPOINT = System.getenv().getOrDefault(
        "AAS_ENDPOINT", "http://localhost:8081");
    
    private static final OkHttpClient httpClient = new OkHttpClient();
    private static OpcUaClient opcUaClient;

    public static void main(String[] args) throws Exception {
        System.out.println("Starting OPC UA -> AAS Bridge...");
        
        // Create OPC UA client
        opcUaClient = OpcUaClient.create(OPC_UA_ENDPOINT);
        opcUaClient.connect().get();
        System.out.println("Connected to OPC UA server: " + OPC_UA_ENDPOINT);

        // Start OPC UA -> AAS monitoring (Hoist Speed)
        startOpcUaToAasMonitoring();

        System.out.println("Bridge is running in OPC UA -> AAS mode. Press Ctrl+C to stop.");

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

        // Create monitored items for all speed nodes
        List<MonitoredItemCreateRequest> requests = List.of(
            createMonitoredItemRequest(OPC_UA_HOIST_SPEED_NODE, 1),
            createMonitoredItemRequest(OPC_UA_TROLLEY_SPEED_NODE, 2),
            createMonitoredItemRequest(OPC_UA_BRIDGE_SPEED_NODE, 3)
        );

        // Subscribe to value changes
        subscription.createMonitoredItems(
            TimestampsToReturn.Both,
            requests,
            (item, id) -> item.setValueConsumer((monitoredItem, value) -> {
                try {
                    String nodeId = monitoredItem.getReadValueId().getNodeId().toParseableString();
                    updateAasProperty(nodeId, value);
                } catch (Exception e) {
                    System.err.println("Error updating AAS: " + e.getMessage());
                }
            })
        ).get();

        System.out.println("✓ OPC UA -> AAS: Monitoring Hoist_Speed, Trolley_Speed, Bridge_Speed");
    }

    /**
     * Helper method to create a monitored item request
     */
    private static MonitoredItemCreateRequest createMonitoredItemRequest(String nodeId, int clientHandle) {
        ReadValueId readValueId = new ReadValueId(
            NodeId.parse(nodeId),
            AttributeId.Value.uid(),
            null,
            null
        );

        MonitoringParameters parameters = new MonitoringParameters(
            UInteger.valueOf(clientHandle),
            1000.0,               // sampling interval
            null,                 // filter
            UInteger.valueOf(10), // queue size
            true                  // discard oldest
        );

        return new MonitoredItemCreateRequest(
            readValueId,
            MonitoringMode.Reporting,
            parameters
        );
    }



    /**
     * Update AAS property when OPC UA value changes
     */
    private static void updateAasProperty(String opcNodeId, DataValue value) throws Exception {
        Object opcValue = value.getValue().getValue();
        
        // Map OPC UA node ID to AAS property ID
        String aasPropertyId;
        if (opcNodeId.contains("Hoist.Speed")) {
            aasPropertyId = "Hoist_Speed";
        } else if (opcNodeId.contains("Trolley.Speed")) {
            aasPropertyId = "Trolley_Speed";
        } else if (opcNodeId.contains("Bridge.Speed")) {
            aasPropertyId = "Bridge_Speed";
        } else {
            System.err.println("Unknown OPC UA node: " + opcNodeId);
            return;
        }

        System.out.println("OPC UA " + aasPropertyId + " changed: " + opcValue);

        // Build AAS property value update URL for BaSyx v2
        String url = String.format("%s/submodels/%s/submodel-elements/%s/$value",
            AAS_ENDPOINT, SUBMODEL_ID, aasPropertyId);

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
                System.out.println("  ✓ Updated AAS " + aasPropertyId + " = " + opcValue);
            } else {
                System.err.println("  ✗ Failed to update AAS " + aasPropertyId + ". Status: " + response.code());
            }
        }
    }

   
}
