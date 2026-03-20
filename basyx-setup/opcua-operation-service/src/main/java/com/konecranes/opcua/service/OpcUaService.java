package com.konecranes.opcua.service;

import org.eclipse.milo.opcua.sdk.client.OpcUaClient;
import org.eclipse.milo.opcua.sdk.client.api.config.OpcUaClientConfig;
import org.eclipse.milo.opcua.sdk.client.api.identity.AnonymousProvider;
import org.eclipse.milo.opcua.sdk.client.api.identity.IdentityProvider;
import org.eclipse.milo.opcua.sdk.client.api.identity.UsernameProvider;
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
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Service for OPC UA communication
 */
@Service
public class OpcUaService {

    private static final Logger logger = LoggerFactory.getLogger(OpcUaService.class);

    @Value("${opcua.endpoint:opc.tcp://10.210.1.12:4840}")
    private String opcUaEndpoint;

    @Value("${opcua.auth-mode:anonymous}")
    private String authMode;

    @Value("${opcua.username:}")
    private String username;

    @Value("${opcua.password:}")
    private String password;

    @Value("${opcua.security-mode:None}")
    private String securityMode;

    @Value("${opcua.security-policy:None}")
    private String securityPolicyName;

    @Value("${opcua.pulse.edge-trigger-enabled:false}")
    private boolean edgeTriggerEnabled;

    @Value("${opcua.pulse.leading-false-delay-ms:100}")
    private long leadingFalseDelayMs;

    @Value("${opcua.write.boolean-numeric-fallback-enabled:true}")
    private boolean booleanNumericFallbackEnabled;

    private OpcUaClient client;
    private final AtomicBoolean hasLoggedDisconnected = new AtomicBoolean(false);

    @PostConstruct
    public void init() {
        logger.info("Initializing OPC UA service for endpoint: {} (authMode={}, securityMode={}, securityPolicy={})",
                opcUaEndpoint, authMode, securityMode, securityPolicyName);

        if (!"username".equalsIgnoreCase(authMode)
                && ((username != null && !username.isBlank()) || (password != null && !password.isBlank()))) {
            logger.warn("opcua.auth-mode is '{}' so opcua.username/opcua.password are ignored", authMode);
        }

        try {
            ensureConnected();
            logger.info("✓ Connected to OPC UA server successfully at {}", opcUaEndpoint);
        } catch (Exception e) {
            logger.warn("OPC UA not reachable at startup (service will keep running and retry on operations): {}", e.getMessage());
        }
    }

    private synchronized void ensureConnected() throws Exception {
        if (client == null) {
            client = OpcUaClient.create(buildClientConfig());
        }

        try {
            client.connect().get();
            hasLoggedDisconnected.set(false);
        } catch (Exception firstFailure) {
            logger.warn("OPC UA connect failed, recreating client and retrying: {}", firstFailure.getMessage());
            safeDisconnect();
            client = OpcUaClient.create(buildClientConfig());
            client.connect().get();
            hasLoggedDisconnected.set(false);
        }
    }

    private OpcUaClientConfig buildClientConfig() {
        MessageSecurityMode configuredSecurityMode = parseSecurityMode(securityMode);
        SecurityPolicy configuredSecurityPolicy = parseSecurityPolicy(securityPolicyName);

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
        
        UserTokenPolicy[] userTokenPolicies = new UserTokenPolicy[]{buildUserTokenPolicy()};
        
        // Create endpoint directly without discovery to avoid localhost resolution issues
        EndpointDescription endpoint = new EndpointDescription(
                opcUaEndpoint,
                serverApp,
                ByteString.NULL_VALUE,
                configuredSecurityMode,
                configuredSecurityPolicy.getUri(),
                userTokenPolicies,
                "http://opcfoundation.org/UA-Profile/Transport/uatcp-uasc-uabinary",
                null
        );
        
        return OpcUaClientConfig.builder()
                .setApplicationName(LocalizedText.english("BaSyx OPC UA Operation Service"))
                .setApplicationUri("urn:basyx:opcua:operation:service")
                .setEndpoint(endpoint)
                .setIdentityProvider(buildIdentityProvider())
                .build();
    }

    private UserTokenPolicy buildUserTokenPolicy() {
        if ("username".equalsIgnoreCase(authMode)) {
            return new UserTokenPolicy(
                    "username",
                    UserTokenType.UserName,
                    null,
                    null,
                    null
            );
        }

        return new UserTokenPolicy(
                "anonymous",
                UserTokenType.Anonymous,
                null,
                null,
                null
        );
    }

    private IdentityProvider buildIdentityProvider() {
        if ("username".equalsIgnoreCase(authMode)) {
            if (username == null || username.isBlank()) {
                throw new IllegalStateException("opcua.auth-mode is 'username' but opcua.username is empty");
            }
            return new UsernameProvider(username, password == null ? "" : password);
        }

        return new AnonymousProvider();
    }

    private MessageSecurityMode parseSecurityMode(String configuredValue) {
        if (configuredValue == null || configuredValue.isBlank()) {
            return MessageSecurityMode.None;
        }

        return switch (configuredValue.trim().toLowerCase()) {
            case "none" -> MessageSecurityMode.None;
            case "sign" -> MessageSecurityMode.Sign;
            case "signandencrypt", "sign_and_encrypt", "sign-and-encrypt" -> MessageSecurityMode.SignAndEncrypt;
            default -> throw new IllegalStateException("Unsupported opcua.security-mode: " + configuredValue);
        };
    }

    private SecurityPolicy parseSecurityPolicy(String configuredValue) {
        if (configuredValue == null || configuredValue.isBlank()) {
            return SecurityPolicy.None;
        }

        return switch (configuredValue.trim().toLowerCase()) {
            case "none" -> SecurityPolicy.None;
            case "basic128rsa15" -> SecurityPolicy.Basic128Rsa15;
            case "basic256" -> SecurityPolicy.Basic256;
            case "basic256sha256" -> SecurityPolicy.Basic256Sha256;
            case "aes128_shar256_rsaoaep", "aes128-sha256-rsaoaep", "aes128_sha256_rsaoaep" -> SecurityPolicy.Aes128_Sha256_RsaOaep;
            case "aes256_shar256_rsapss", "aes256-sha256-rsapss", "aes256_sha256_rsapss" -> SecurityPolicy.Aes256_Sha256_RsaPss;
            default -> throw new IllegalStateException("Unsupported opcua.security-policy: " + configuredValue);
        };
    }

    private synchronized void safeDisconnect() {
        if (client == null) {
            return;
        }
        try {
            client.disconnect().get();
        } catch (Exception ignored) {
        }
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
        try {
            ensureConnected();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw e;
        } catch (Exception e) {
            if (hasLoggedDisconnected.compareAndSet(false, true)) {
                logger.error("OPC UA connection unavailable", e);
            }
            throw new RuntimeException("OPC UA connection unavailable: " + e.getMessage(), e);
        }

        NodeId node = NodeId.parse(nodeId);

        // Strategy 1: value-attribute write via bulk API
        StatusCode status = client.writeValues(
            List.of(node),
            List.of(DataValue.valueOnly(new Variant(value)))
        ).get().get(0);

        if (status.isGood()) {
            logger.info("Successfully wrote {} to node: {} (strategy=value-attribute)", value, nodeId);
            return;
        }

        // Strategy 2: direct writeValue fallback
        status = client.writeValue(node, DataValue.valueOnly(new Variant(value))).get();
        if (status.isGood()) {
            logger.info("Successfully wrote {} to node: {} (strategy=writeValue)", value, nodeId);
            return;
        }

        // Strategy 3: optional numeric BOOL fallback (some servers expose BOOL as Int16)
        if (booleanNumericFallbackEnabled) {
            short numericValue = (short) (value ? 1 : 0);
            status = client.writeValue(node, DataValue.valueOnly(new Variant(numericValue))).get();
            if (status.isGood()) {
                logger.info("Successfully wrote {} to node: {} (strategy=numeric-int16)", numericValue, nodeId);
                return;
            }
        }

        logNodeWriteDiagnostics(nodeId, node);
        logger.error("Failed to write to node: {}. Status: {}", nodeId, status);
        throw new RuntimeException("OPC UA write failed: " + status);
    }

    private void logNodeWriteDiagnostics(String nodeId, NodeId node) {
        try {
            DataValue currentValue = client.readValue(0, TimestampsToReturn.Neither, node).get();
            Object currentRaw = currentValue.getValue() != null ? currentValue.getValue().getValue() : null;
            String currentType = currentRaw != null ? currentRaw.getClass().getName() : "null";

            logger.warn("Write diagnostics for {} -> currentStatus={}, currentType={}, currentValue={}",
                    nodeId,
                    currentValue.getStatusCode(),
                    currentType,
                    currentRaw);
        } catch (Exception diagEx) {
            logger.warn("Could not collect write diagnostics for {}: {}", nodeId, diagEx.getMessage());
        }
    }

    /**
     * Read a value from an OPC UA node
     */
    public Object readValue(String nodeId) throws ExecutionException, InterruptedException {
        try {
            ensureConnected();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw e;
        } catch (Exception e) {
            if (hasLoggedDisconnected.compareAndSet(false, true)) {
                logger.error("OPC UA connection unavailable", e);
            }
            throw new RuntimeException("OPC UA connection unavailable: " + e.getMessage(), e);
        }

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
     * Read a numeric value and convert it to double
     */
    public double readDouble(String nodeId) throws ExecutionException, InterruptedException {
        Object value = readValue(nodeId);

        if (value instanceof Number number) {
            return number.doubleValue();
        }

        if (value instanceof String text) {
            return Double.parseDouble(text);
        }

        throw new RuntimeException("Value at node is not numeric: " + nodeId + " (" + value + ")");
    }

    /**
     * Read a numeric value and convert it to long
     */
    public long readLong(String nodeId) throws ExecutionException, InterruptedException {
        Object value = readValue(nodeId);

        if (value instanceof Number number) {
            return number.longValue();
        }

        if (value instanceof String text) {
            return Long.parseLong(text);
        }

        throw new RuntimeException("Value at node is not integral: " + nodeId + " (" + value + ")");
    }

    /**
     * Write an Int16 value to an OPC UA node
     */
    public void writeInt16(String nodeId, int value) throws ExecutionException, InterruptedException {
        try {
            ensureConnected();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw e;
        } catch (Exception e) {
            if (hasLoggedDisconnected.compareAndSet(false, true)) {
                logger.error("OPC UA connection unavailable", e);
            }
            throw new RuntimeException("OPC UA connection unavailable: " + e.getMessage(), e);
        }

        NodeId node = NodeId.parse(nodeId);
        DataValue dataValue = new DataValue(new Variant((short) value));

        StatusCode status = client.writeValue(node, dataValue).get();

        if (status.isGood()) {
            logger.info("Successfully wrote {} (Int16) to node: {}", value, nodeId);
            return;
        }

        // Fallback to generic numeric write handling
        logger.warn("Int16 write failed for {}, trying numeric fallback", nodeId);
        writeDouble(nodeId, value);
    }

    /**
     * Write an Int32 value to an OPC UA node
     */
    public void writeInt32(String nodeId, int value) throws ExecutionException, InterruptedException {
        try {
            ensureConnected();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw e;
        } catch (Exception e) {
            if (hasLoggedDisconnected.compareAndSet(false, true)) {
                logger.error("OPC UA connection unavailable", e);
            }
            throw new RuntimeException("OPC UA connection unavailable: " + e.getMessage(), e);
        }

        NodeId node = NodeId.parse(nodeId);
        DataValue dataValue = new DataValue(new Variant(value));

        StatusCode status = client.writeValue(node, dataValue).get();

        if (status.isGood()) {
            logger.info("Successfully wrote {} (Int32) to node: {}", value, nodeId);
            return;
        }

        logger.warn("Int32 write failed for {}, trying numeric fallback", nodeId);
        writeDouble(nodeId, value);
    }

    /**
     * Write a double value to an OPC UA node
     * If double fails with type mismatch, tries Float, then Int32
     */
    public void writeDouble(String nodeId, double value) throws ExecutionException, InterruptedException {
        try {
            ensureConnected();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw e;
        } catch (Exception e) {
            if (hasLoggedDisconnected.compareAndSet(false, true)) {
                logger.error("OPC UA connection unavailable", e);
            }
            throw new RuntimeException("OPC UA connection unavailable: " + e.getMessage(), e);
        }

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
        if (edgeTriggerEnabled) {
            logger.info("Writing edge-trigger pulse to node: {} (leadingFalseDelayMs={}, pulseDurationMs={})",
                    nodeId, leadingFalseDelayMs, durationMs);
            writeBoolean(nodeId, false);
            if (leadingFalseDelayMs > 0) {
                Thread.sleep(leadingFalseDelayMs);
            }
        }

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
