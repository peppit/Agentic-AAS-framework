package com.openindustryproject.mqttbridge.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken;
import org.eclipse.paho.client.mqttv3.MqttCallback;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

@Service
public class MqttCommandBridgeService implements MqttCallback {

    private static final Logger logger = LoggerFactory.getLogger(MqttCommandBridgeService.class);

    @Value("${bridge.mqtt.broker-url}")
    private String brokerUrl;

    @Value("${bridge.mqtt.client-id}")
    private String clientId;

    @Value("${bridge.mqtt.topic-filter}")
    private String topicFilter;

    @Value("${bridge.mqtt.qos:1}")
    private int qos;

    @Value("${bridge.mqtt.reply-topic-prefix:}")
    private String replyTopicPrefix;

    @Value("${bridge.invoke.timeout-ms:8000}")
    private long timeoutMs;

    @Value("${bridge.invoke.conveyor.running-url:}")
    private String runningInvokeUrl;

    @Value("${bridge.invoke.conveyor.running-idshort:running}")
    private String runningIdShort;

    @Value("${bridge.invoke.conveyor.speed-url:}")
    private String speedInvokeUrl;

    @Value("${bridge.invoke.conveyor.speed-idshort:speed}")
    private String speedIdShort;

    @Value("${bridge.invoke.robot.move-box-url:}")
    private String moveBoxInvokeUrl;

    @Value("${bridge.invoke.robot.move-box-idshort:moveBox}")
    private String moveBoxIdShort;

    @Value("${bridge.invoke.robot.move-to-home-url:}")
    private String moveToHomeInvokeUrl;

    @Value("${bridge.invoke.robot.move-to-home-idshort:moveToHome}")
    private String moveToHomeIdShort;

    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient httpClient = HttpClient.newBuilder().build();

    private MqttClient mqttClient;

    @PostConstruct
    public void start() throws MqttException {
        MqttConnectOptions options = new MqttConnectOptions();
        options.setAutomaticReconnect(true);
        options.setCleanSession(true);
        options.setConnectionTimeout(10);

        mqttClient = new MqttClient(brokerUrl, clientId, new MemoryPersistence());
        mqttClient.setCallback(this);
        mqttClient.connect(options);
        mqttClient.subscribe(topicFilter, qos);

        logger.info("Connected to MQTT broker {} and subscribed to {}", brokerUrl, topicFilter);
    }

    @PreDestroy
    public void stop() {
        if (mqttClient == null) {
            return;
        }

        try {
            if (mqttClient.isConnected()) {
                mqttClient.disconnect();
            }
            mqttClient.close();
        } catch (MqttException e) {
            logger.warn("Error while closing MQTT bridge client", e);
        }
    }

    @Override
    public void connectionLost(Throwable cause) {
        logger.warn("MQTT connection lost: {}", cause.getMessage());
    }

    @Override
    public void messageArrived(String topic, MqttMessage message) {
        String payload = new String(message.getPayload(), StandardCharsets.UTF_8).trim();
        logger.info("Received MQTT command on {} payload={}", topic, payload);

        if (topic.endsWith("/running")) {
            handleRunning(topic, payload);
            return;
        }

        if (topic.endsWith("/speed")) {
            handleSpeed(topic, payload);
            return;
        }

        if (topic.endsWith("/moveBox")) {
            handleMoveBox(topic, payload);
            return;
        }

        if (topic.endsWith("/moveToHome")) {
            handleMoveToHome(topic, payload);
            return;
        }

        logger.warn("Ignoring unsupported topic {}", topic);
    }

    @Override
    public void deliveryComplete(IMqttDeliveryToken token) {
    }

    private void handleRunning(String topic, String payload) {
        if (runningInvokeUrl == null || runningInvokeUrl.isBlank()) {
            logger.warn("running-url is empty; skipping running command");
            return;
        }

        String requestId = extractRequestId(payload);
        boolean value;
        try {
            value = parseBooleanValue(payload, "running");
        } catch (Exception e) {
            logger.error("Invalid running command payload", e);
            publishReply("running", requestId, false, e.getMessage());
            return;
        }

        try {
            HttpResponse<String> response = invokeOperation(runningInvokeUrl, runningIdShort, "xs:boolean", String.valueOf(value));
            boolean success = response.statusCode() >= 200 && response.statusCode() < 300;
            publishReply("running", requestId, success, response.body());
        } catch (Exception e) {
            logger.error("Failed to invoke running operation", e);
            publishReply("running", requestId, false, e.getMessage());
        }
    }

    private void handleSpeed(String topic, String payload) {
        if (speedInvokeUrl == null || speedInvokeUrl.isBlank()) {
            logger.warn("speed-url is empty; skipping speed command");
            return;
        }

        String requestId = extractRequestId(payload);
        double value;
        try {
            value = parseDoubleValue(payload, "speed");
        } catch (Exception e) {
            logger.error("Invalid speed command payload", e);
            publishReply("speed", requestId, false, e.getMessage());
            return;
        }

        try {
            HttpResponse<String> response = invokeOperation(speedInvokeUrl, speedIdShort, "xs:double", String.valueOf(value));
            boolean success = response.statusCode() >= 200 && response.statusCode() < 300;
            publishReply("speed", requestId, success, response.body());
        } catch (Exception e) {
            logger.error("Failed to invoke speed operation", e);
            publishReply("speed", requestId, false, e.getMessage());
        }
    }

    private void handleMoveBox(String topic, String payload) {
        if (moveBoxInvokeUrl == null || moveBoxInvokeUrl.isBlank()) {
            logger.warn("move-box-url is empty; skipping moveBox command");
            return;
        }

        String requestId = extractRequestId(payload);
        String conveyor;
        String pallet;

        try {
            JsonNode node = mapper.readTree(payload);
            
            // Match the loose fallback extraction logic used in your controller
            conveyor = node.has("conveyor") ? node.get("conveyor").asText() : 
                    node.has("Conveyor1") ? node.get("Conveyor1").asText() : "";
                    
            pallet = node.has("pallet") ? node.get("pallet").asText() : 
                    node.has("Pallet1") ? node.get("Pallet1").asText() : "";

            if (conveyor.isBlank() || pallet.isBlank()) {
                throw new IllegalArgumentException("Missing required parameters: conveyor/Conveyor1 or pallet/Pallet1");
            }
        } catch (Exception e) {
            logger.error("Invalid movebox command payload", e);
            publishReply("moveBox", requestId, false, e.getMessage());
            return;
        }

        try {
            // Build an HTTP request with multiple input arguments
            String body = buildMultiInvokeRequest(moveBoxIdShort, conveyor, pallet);

            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(moveBoxInvokeUrl))
                    .timeout(Duration.ofMillis(timeoutMs))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(body))
                    .build();

            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            logger.info("Invoke {} returned status {}", moveBoxInvokeUrl, response.statusCode());

            boolean success = response.statusCode() >= 200 && response.statusCode() < 300;
            publishReply("moveBox", requestId, success, response.body());
        } catch (Exception e) {
            logger.error("Failed to invoke movebox operation", e);
            publishReply("moveBox", requestId, false, e.getMessage());
        }
    }

    private void handleMoveToHome(String topic, String payload) {
        if (moveHomeInvokeUrl == null || moveHomeInvokeUrl.isBlank()) {
            logger.warn("move-to-home-url is empty; skipping moveToHome command");
            return;
        }

        String requestId = extractRequestId(payload);
        boolean value;
        try {
            value = parseBooleanValue(payload, "move");
        } catch (Exception e) {
            logger.error("Invalid moveToHome command payload", e);
            publishReply("moveToHome", requestId, false, e.getMessage());
            return;
        }

        try {
            HttpResponse<String> response = invokeOperation(moveHomeInvokeUrl, moveHomeIdShort, "xs:boolean", String.valueOf(value));
            boolean success = response.statusCode() >= 200 && response.statusCode() < 300;
            publishReply("moveToHome", requestId, success, response.body());
        } catch (Exception e) {
            logger.error("Failed to invoke moveToHome operation", e);
            publishReply("moveToHome", requestId, false, e.getMessage());
        }
    }

    private HttpResponse<String> invokeOperation(String url, String idShort, String valueType, String value)
            throws IOException, InterruptedException {
        String body = buildInvokeRequest(idShort, valueType, value);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .timeout(Duration.ofMillis(timeoutMs))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();

        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        logger.info("Invoke {} returned status {}", url, response.statusCode());
        return response;
    }

    private String buildInvokeRequest(String idShort, String valueType, String value) {
        ObjectNode root = mapper.createObjectNode();
        ArrayNode inputArguments = root.putArray("inputArguments");

        ObjectNode wrapper = inputArguments.addObject();
        ObjectNode valueNode = wrapper.putObject("value");
        valueNode.put("modelType", "Property");
        valueNode.put("idShort", idShort);
        valueNode.put("valueType", valueType);
        valueNode.put("value", value);

        root.putArray("inoutputArguments");
        root.put("requestedTimeout", timeoutMs);

        return root.toString();
    }

    private String buildMultiInvokeRequest(String idShort, String conveyor, String pallet) {
        ObjectNode root = mapper.createObjectNode();
        ArrayNode inputArguments = root.putArray("inputArguments");

        // Argument 1: Conveyor
        ObjectNode wrapper1 = inputArguments.addObject();
        ObjectNode valueNode1 = wrapper1.putObject("value");
        valueNode1.put("modelType", "Property");
        valueNode1.put("idShort", "Conveyor1");
        valueNode1.put("valueType", "xs:string");
        valueNode1.put("value", conveyor);

        // Argument 2: Pallet
        ObjectNode wrapper2 = inputArguments.addObject();
        ObjectNode valueNode2 = wrapper2.putObject("value");
        valueNode2.put("modelType", "Property");
        valueNode2.put("idShort", "Pallet1");
        valueNode2.put("valueType", "xs:string");
        valueNode2.put("value", pallet);

        root.putArray("inoutputArguments");
        root.put("requestedTimeout", timeoutMs);

        return root.toString();
   }

    private String extractRequestId(String payload) {
        try {
            JsonNode node = mapper.readTree(payload);
            JsonNode requestIdNode = node.get("requestId");
            return requestIdNode != null ? requestIdNode.asText() : "";
        } catch (Exception ignored) {
            return "";
        }
    }

    private boolean parseBooleanValue(String payload, String fieldName) throws IOException {
        if (!payload.startsWith("{")) {
            return parseBooleanLiteral(payload);
        }

        JsonNode node = mapper.readTree(payload);
        JsonNode valueNode = node.get("value");
        if (valueNode != null) {
            return parseBooleanNode(valueNode);
        }

        JsonNode fieldNode = node.get(fieldName);
        if (fieldNode != null) {
            return parseBooleanNode(fieldNode);
        }

        throw new IllegalArgumentException("Missing boolean value in payload");
    }

    private double parseDoubleValue(String payload, String fieldName) throws IOException {
        if (!payload.startsWith("{")) {
            return Double.parseDouble(payload);
        }

        JsonNode node = mapper.readTree(payload);
        JsonNode valueNode = node.get("value");
        if (valueNode != null && valueNode.isNumber()) {
            return valueNode.asDouble();
        }

        if (valueNode != null && valueNode.isTextual()) {
            return Double.parseDouble(valueNode.asText());
        }

        JsonNode fieldNode = node.get(fieldName);
        if (fieldNode != null && fieldNode.isNumber()) {
            return fieldNode.asDouble();
        }

        if (fieldNode != null && fieldNode.isTextual()) {
            return Double.parseDouble(fieldNode.asText());
        }

        throw new IllegalArgumentException("Missing numeric value in payload");
    }

    private boolean parseBooleanNode(JsonNode node) {
        if (node.isBoolean()) {
            return node.asBoolean();
        }

        if (node.isTextual()) {
            return parseBooleanLiteral(node.asText());
        }

        if (node.isInt()) {
            return node.asInt() != 0;
        }

        throw new IllegalArgumentException("Unsupported boolean value: " + node);
    }

    private boolean parseBooleanLiteral(String raw) {
        String normalized = raw.trim().toLowerCase();
        return normalized.equals("true") || normalized.equals("1") || normalized.equals("on");
    }

    private void publishReply(String operation, String requestId, boolean success, String message) {
        if (replyTopicPrefix == null || replyTopicPrefix.isBlank()) {
            return;
        }

        try {
            ObjectNode reply = mapper.createObjectNode();
            reply.put("operation", operation);
            reply.put("requestId", requestId == null ? "" : requestId);
            reply.put("success", success);
            reply.put("message", message == null ? "" : message);

            String topic = replyTopicPrefix + "/" + operation;
            mqttClient.publish(topic, new MqttMessage(reply.toString().getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            logger.warn("Failed to publish MQTT reply", e);
        }
    }
}
