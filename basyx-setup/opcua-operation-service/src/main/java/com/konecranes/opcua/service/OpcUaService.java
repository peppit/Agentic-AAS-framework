package com.konecranes.opcua.service;

import org.eclipse.milo.opcua.sdk.client.OpcUaClient;
import org.eclipse.milo.opcua.sdk.client.api.config.OpcUaClientConfig;
import org.eclipse.milo.opcua.stack.core.security.SecurityPolicy;
import org.eclipse.milo.opcua.stack.core.types.builtin.ByteString;
import org.eclipse.milo.opcua.stack.core.types.builtin.DataValue;
import org.eclipse.milo.opcua.stack.core.types.builtin.LocalizedText;
import org.eclipse.milo.opcua.stack.core.types.builtin.NodeId;
import org.eclipse.milo.opcua.stack.core.types.enumerated.ApplicationType;
import org.eclipse.milo.opcua.stack.core.types.enumerated.MessageSecurityMode;
import org.eclipse.milo.opcua.stack.core.types.enumerated.UserTokenType;
import org.eclipse.milo.opcua.stack.core.types.structured.ApplicationDescription;
import org.eclipse.milo.opcua.stack.core.types.structured.EndpointDescription;
import org.eclipse.milo.opcua.stack.core.types.structured.UserTokenPolicy;
import org.eclipse.milo.opcua.stack.core.types.builtin.StatusCode;
import org.eclipse.milo.opcua.stack.core.types.builtin.Variant;
import org.eclipse.milo.opcua.stack.core.types.enumerated.TimestampsToReturn;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.util.concurrent.ExecutionException;

/**
 * Service for OPC UA communication
 */
@Service
public class OpcUaService {

    private static final Logger logger = LoggerFactory.getLogger(OpcUaService.class);

    @Value("${opcua.endpoint:opc.tcp://host.docker.internal:4840}")
    private String opcUaEndpoint;

    private OpcUaClient client;

    @PostConstruct
    public void init() throws Exception {
        logger.info("Connecting to OPC UA server: {}", opcUaEndpoint);
        
        // Create ApplicationDescription for the server
        ApplicationDescription serverApp = new ApplicationDescription(
                "urn:opcua:server",
                null,
                LocalizedText.english("OPC UA Server"),
                ApplicationType.Server,
                null,
                null,
                null
        );
        
        // Create anonymous user token policy
        UserTokenPolicy[] userTokenPolicies = new UserTokenPolicy[]{
                new UserTokenPolicy(
                        "anonymous",
                        UserTokenType.Anonymous,
                        null,
                        null,
                        null
                )
        };
        
        // Create endpoint directly without discovery to avoid localhost resolution issues
        EndpointDescription endpoint = new EndpointDescription(
                opcUaEndpoint,
                serverApp,
                ByteString.NULL_VALUE,
                MessageSecurityMode.None,
                SecurityPolicy.None.getUri(),
                userTokenPolicies,
                "http://opcfoundation.org/UA-Profile/Transport/uatcp-uasc-uabinary",
                null
        );
        
        OpcUaClientConfig config = OpcUaClientConfig.builder()
                .setApplicationName(LocalizedText.english("BaSyx OPC UA Operation Service"))
                .setApplicationUri("urn:basyx:opcua:operation:service")
                .setEndpoint(endpoint)
                .build();

        client = OpcUaClient.create(config);
        client.connect().get();
        logger.info("✓ Connected to OPC UA server successfully at {}", opcUaEndpoint);
    }

    @PreDestroy
    public void destroy() {
        if (client != null) {
            try {
                client.disconnect().get();
                logger.info("Disconnected from OPC UA server");
            } catch (Exception e) {
                logger.error("Error disconnecting from OPC UA server", e);
            }
        }
    }

    /**
     * Write a boolean value to an OPC UA node
     */
    public void writeBoolean(String nodeId, boolean value) throws ExecutionException, InterruptedException {
        NodeId node = NodeId.parse(nodeId);
        DataValue dataValue = new DataValue(new Variant(value));
        
        StatusCode status = client.writeValue(node, dataValue).get();
        
        if (status.isGood()) {
            logger.info("Successfully wrote {} to node: {}", value, nodeId);
        } else {
            logger.error("Failed to write to node: {}. Status: {}", nodeId, status);
            throw new RuntimeException("OPC UA write failed: " + status);
        }
    }

    /**
     * Read a value from an OPC UA node
     */
    public Object readValue(String nodeId) throws ExecutionException, InterruptedException {
        NodeId node = NodeId.parse(nodeId);
        DataValue value = client.readValue(0, TimestampsToReturn.Neither, node).get();
        
        if (value.getStatusCode().isGood()) {
            Object result = value.getValue().getValue();
            logger.info("Read value from {}: {}", nodeId, result);
            return result;
        } else {
            logger.error("Failed to read from node: {}. Status: {}", nodeId, value.getStatusCode());
            throw new RuntimeException("OPC UA read failed: " + value.getStatusCode());
        }
    }

    /**
     * Write a double value to an OPC UA node
     * If double fails with type mismatch, tries Float, then Int32
     */
    public void writeDouble(String nodeId, double value) throws ExecutionException, InterruptedException {
        NodeId node = NodeId.parse(nodeId);
        
        // Try Double first
        DataValue dataValue = new DataValue(new Variant(value));
        StatusCode status = client.writeValue(node, dataValue).get();
        
        if (status.isGood()) {
            logger.info("Successfully wrote {} (Double) to node: {}", value, nodeId);
            return;
        }
        
        // If type mismatch, try Float
        if (status.getValue() == 0x80740000L) { // Bad_TypeMismatch
            logger.warn("Double type mismatch for {}, trying Float", nodeId);
            dataValue = new DataValue(new Variant((float)value));
            status = client.writeValue(node, dataValue).get();
            
            if (status.isGood()) {
                logger.info("Successfully wrote {} (Float) to node: {}", value, nodeId);
                return;
            }
            
            // If still failing, try Int32
            logger.warn("Float type mismatch for {}, trying Int32", nodeId);
            dataValue = new DataValue(new Variant((int)value));
            status = client.writeValue(node, dataValue).get();
            
            if (status.isGood()) {
                logger.info("Successfully wrote {} (Int32) to node: {}", (int)value, nodeId);
                return;
            }
        }
        
        logger.error("Failed to write to node: {}. Status: {}", nodeId, status);
        throw new RuntimeException("OPC UA write failed: " + status);
    }

    /**
     * Write a boolean pulse (true, then false after delay)
     */
    public void writePulse(String nodeId, long durationMs) throws ExecutionException, InterruptedException {
        writeBoolean(nodeId, true);
        
        // Spawn thread for delayed reset
        new Thread(() -> {
            try {
                Thread.sleep(durationMs);
                writeBoolean(nodeId, false);
            } catch (Exception e) {
                logger.error("Error resetting pulse for node: {}", nodeId, e);
            }
        }).start();
    }
}
