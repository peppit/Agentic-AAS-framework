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
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

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

    @Value("${bridge.mqtt.command-prefix:oip/command}")
    private String commandPrefix;

    @Value("${bridge.aas.base-url:http://aas-env:8081}")
    private String aasBaseUrl;

    @Value("${bridge.station.bindings:}")
    private String stationBindingsRaw;

    @Value("${bridge.station.bindings-file:}")
    private String stationBindingsFile;

    @Value("${bridge.invoke.timeout-ms:8000}")
    private long timeoutMs;

    @Value("${bridge.invoke.conveyor.running-url:}")
    private String runningInvokeUrl;

    @Value("${bridge.invoke.conveyor.running-idshort:running}")
    private String runningIdShort;

    @Value("${bridge.invoke.conveyor.running-operation-idshort:Running}")
    private String runningOperationIdShort;

    @Value("${bridge.invoke.conveyor.speed-url:}")
    private String speedInvokeUrl;

    @Value("${bridge.invoke.conveyor.speed-idshort:speed}")
    private String speedIdShort;

    @Value("${bridge.invoke.conveyor.speed-operation-idshort:Speed}")
    private String speedOperationIdShort;

    @Value("${bridge.invoke.robot.move-box-url:}")
    private String moveBoxInvokeUrl;

    @Value("${bridge.invoke.robot.move-box-idshort:moveBox}")
    private String moveBoxIdShort;

    @Value("${bridge.invoke.robot.move-box-operation-idshort:MoveBox}")
    private String moveBoxOperationIdShort;

    @Value("${bridge.invoke.robot.move-to-home-url:}")
    private String moveToHomeInvokeUrl;

    @Value("${bridge.invoke.robot.move-to-home-idshort:moveToHome}")
    private String moveToHomeIdShort;

    @Value("${bridge.invoke.robot.move-to-home-operation-idshort:MoveToHome}")
    private String moveToHomeOperationIdShort;

    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient httpClient = HttpClient.newBuilder().build();
    private final Map<String, StationBinding> stationBindings = new HashMap<>();

    private MqttClient mqttClient;

    @PostConstruct
    public void start() throws MqttException {
        loadStationBindings();

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

        TopicRoute route = parseTopicRoute(topic);
        if (route == null) {
            logger.warn("Ignoring topic that does not match command-prefix '{}': {}", commandPrefix, topic);
            return;
        }

        switch (route.action) {
            case "running":
            case "run":
                handleRunning(route, payload);
                return;
            case "speed":
            case "setspeed":
                handleSpeed(route, payload);
                return;
            case "movebox":
                handleMoveBox(route, payload);
                return;
            case "movetohome":
                handleMoveToHome(route, payload);
                return;
            default:
                logger.warn("Ignoring unsupported action '{}' for station '{}' on topic {}", route.action, route.stationId, topic);
        }
    }

    @Override
    public void deliveryComplete(IMqttDeliveryToken token) {
    }

    private void handleRunning(TopicRoute route, String payload) {
        String invokeUrl = resolveInvokeUrl(route.stationId, "conveyor", runningOperationIdShort, runningInvokeUrl);
        if (invokeUrl == null || invokeUrl.isBlank()) {
            logger.warn("No running invoke URL resolved for station '{}'", route.stationId);
            return;
        }

        String requestId = extractRequestId(payload);
        boolean value;
        try {
            value = parseBooleanValue(payload, "running");
        } catch (Exception e) {
            logger.error("Invalid running command payload", e);
            publishReply(route.stationId, "running", requestId, false, e.getMessage());
            return;
        }

        try {
            HttpResponse<String> response = invokeOperation(invokeUrl, runningIdShort, "xs:boolean", String.valueOf(value));
            boolean success = response.statusCode() >= 200 && response.statusCode() < 300;
            publishReply(route.stationId, "running", requestId, success, response.body());
        } catch (Exception e) {
            logger.error("Failed to invoke running operation", e);
            publishReply(route.stationId, "running", requestId, false, e.getMessage());
        }
    }

    private void handleSpeed(TopicRoute route, String payload) {
        String invokeUrl = resolveInvokeUrl(route.stationId, "conveyor", speedOperationIdShort, speedInvokeUrl);
        if (invokeUrl == null || invokeUrl.isBlank()) {
            logger.warn("No speed invoke URL resolved for station '{}'", route.stationId);
            return;
        }

        String requestId = extractRequestId(payload);
        double value;
        try {
            value = parseDoubleValue(payload, "speed");
        } catch (Exception e) {
            logger.error("Invalid speed command payload", e);
            publishReply(route.stationId, "speed", requestId, false, e.getMessage());
            return;
        }

        try {
            HttpResponse<String> response = invokeOperation(invokeUrl, speedIdShort, "xs:double", String.valueOf(value));
            boolean success = response.statusCode() >= 200 && response.statusCode() < 300;
            publishReply(route.stationId, "speed", requestId, success, response.body());
        } catch (Exception e) {
            logger.error("Failed to invoke speed operation", e);
            publishReply(route.stationId, "speed", requestId, false, e.getMessage());
        }
    }

    private void handleMoveBox(TopicRoute route, String payload) {
        String invokeUrl = resolveInvokeUrl(route.stationId, "robot", moveBoxOperationIdShort, moveBoxInvokeUrl);
        if (invokeUrl == null || invokeUrl.isBlank()) {
            logger.warn("No moveBox invoke URL resolved for station '{}'", route.stationId);
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
            publishReply(route.stationId, "moveBox", requestId, false, e.getMessage());
            return;
        }

        try {
            // Build an HTTP request with multiple input arguments
            String body = buildMultiInvokeRequest(moveBoxIdShort, conveyor, pallet);

            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(invokeUrl))
                    .timeout(Duration.ofMillis(timeoutMs))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(body))
                    .build();

            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            logger.info("Invoke {} returned status {}", invokeUrl, response.statusCode());

            boolean success = response.statusCode() >= 200 && response.statusCode() < 300;
            publishReply(route.stationId, "moveBox", requestId, success, response.body());
        } catch (Exception e) {
            logger.error("Failed to invoke movebox operation", e);
            publishReply(route.stationId, "moveBox", requestId, false, e.getMessage());
        }
    }

    private void handleMoveToHome(TopicRoute route, String payload) {
        String invokeUrl = resolveInvokeUrl(route.stationId, "robot", moveToHomeOperationIdShort, moveToHomeInvokeUrl);
        if (invokeUrl == null || invokeUrl.isBlank()) {
            logger.warn("No moveToHome invoke URL resolved for station '{}'", route.stationId);
            return;
        }

        String requestId = extractRequestId(payload);
        boolean value;
        try {
            value = parseBooleanValue(payload, "move");
        } catch (Exception e) {
            logger.error("Invalid moveToHome command payload", e);
            publishReply(route.stationId, "moveToHome", requestId, false, e.getMessage());
            return;
        }

        try {
            HttpResponse<String> response = invokeOperation(invokeUrl, moveToHomeIdShort, "xs:boolean", String.valueOf(value));
            boolean success = response.statusCode() >= 200 && response.statusCode() < 300;
            publishReply(route.stationId, "moveToHome", requestId, success, response.body());
        } catch (Exception e) {
            logger.error("Failed to invoke moveToHome operation", e);
            publishReply(route.stationId, "moveToHome", requestId, false, e.getMessage());
        }
    }

    private TopicRoute parseTopicRoute(String topic) {
        if (topic == null || topic.isBlank()) {
            return null;
        }

        String[] topicParts = topic.split("/");
        String[] prefixParts = commandPrefix == null ? new String[0] : commandPrefix.split("/");
        if (prefixParts.length == 0 || topicParts.length < prefixParts.length + 2) {
            return null;
        }

        for (int i = 0; i < prefixParts.length; i++) {
            if (!prefixParts[i].equals(topicParts[i])) {
                return null;
            }
        }

        String stationId = topicParts[prefixParts.length].trim();
        String action = topicParts[prefixParts.length + 1].trim().toLowerCase(Locale.ROOT).replace("-", "");
        if (stationId.isEmpty() || action.isEmpty()) {
            return null;
        }
        return new TopicRoute(stationId, action);
    }

    private String resolveInvokeUrl(String stationId, String domain, String operationPathIdShort, String fallbackUrl) {
        StationBinding binding = stationBindings.get(normalizeStationId(stationId));
        if (binding != null) {
            String submodelB64 = "conveyor".equals(domain) ? binding.conveyorSubmodelB64 : binding.robotSubmodelB64;
            if (submodelB64 != null && !submodelB64.isBlank()) {
                return buildInvokeUrl(submodelB64, operationPathIdShort);
            }
        }

        if (fallbackUrl != null && !fallbackUrl.isBlank()) {
            logger.debug("Using static fallback invoke URL for station '{}' and domain '{}'", stationId, domain);
            return fallbackUrl;
        }

        return null;
    }

    private String buildInvokeUrl(String submodelB64, String operationPathIdShort) {
        String base = (aasBaseUrl == null ? "http://aas-env:8081" : aasBaseUrl).replaceAll("/+$", "");
        String opPath = operationPathIdShort == null ? "" : operationPathIdShort.trim();
        return base + "/submodels/" + submodelB64 + "/submodel-elements/" + opPath + "/invoke";
    }

    private void loadStationBindings() {
        stationBindings.clear();

        boolean loadedAny = false;

        if (stationBindingsFile != null && !stationBindingsFile.isBlank()) {
            loadedAny = loadStationBindingsFromFile(stationBindingsFile.trim()) || loadedAny;
        }

        if (stationBindingsRaw != null && !stationBindingsRaw.isBlank()) {
            parseStationBindingsCsv(stationBindingsRaw);
            loadedAny = true;
        }

        if (!loadedAny) {
            logger.info("No station bindings configured. Dynamic routing disabled; static URLs will be used when configured.");
            return;
        }

        logger.info("Loaded {} dynamic station binding(s) for MQTT bridge", stationBindings.size());
    }

    private boolean loadStationBindingsFromFile(String filePath) {
        try {
            Path path = Path.of(filePath);
            if (!Files.exists(path)) {
                logger.warn("Station bindings file does not exist: {}", filePath);
                return false;
            }

            String content = Files.readString(path, StandardCharsets.UTF_8);
            parseStationBindingsJson(content, filePath);
            return true;
        } catch (Exception e) {
            logger.warn("Failed to load station bindings file {}: {}", filePath, e.getMessage());
            return false;
        }
    }

    private void parseStationBindingsCsv(String rawBindings) {
        String[] entries = rawBindings.split("[;,]");
        for (String entry : entries) {
            String trimmed = entry.trim();
            if (trimmed.isEmpty()) {
                continue;
            }

            int separatorIndex = trimmed.indexOf('=');
            if (separatorIndex < 0) {
                separatorIndex = trimmed.indexOf(':');
            }
            if (separatorIndex <= 0 || separatorIndex == trimmed.length() - 1) {
                logger.warn("Ignoring malformed station binding '{}'. Expected stationId=conveyorSubmodelB64|robotSubmodelB64", trimmed);
                continue;
            }

            String stationId = trimmed.substring(0, separatorIndex).trim();
            String rhs = trimmed.substring(separatorIndex + 1).trim();
            String[] ids = rhs.split("\\|", 2);
            String conveyor = ids[0].trim();
            String robot = ids.length > 1 ? ids[1].trim() : conveyor;

            if (stationId.isEmpty() || conveyor.isEmpty()) {
                logger.warn("Ignoring malformed station binding '{}'", trimmed);
                continue;
            }

            stationBindings.put(normalizeStationId(stationId), new StationBinding(conveyor, robot));
        }
    }

    private void parseStationBindingsJson(String json, String sourceName) throws IOException {
        JsonNode root = mapper.readTree(json);
        if (root == null || !root.isObject()) {
            logger.warn("Ignoring station bindings from {}: expected JSON object", sourceName);
            return;
        }

        root.fields().forEachRemaining(entry -> {
            String stationId = entry.getKey();
            JsonNode value = entry.getValue();

            String conveyor = null;
            String robot = null;

            if (value.isTextual()) {
                String[] ids = value.asText().split("\\|", 2);
                conveyor = ids[0].trim();
                robot = ids.length > 1 ? ids[1].trim() : conveyor;
            } else if (value.isObject()) {
                JsonNode conveyorNode = value.get("conveyorSubmodelB64");
                JsonNode robotNode = value.get("robotSubmodelB64");
                if (conveyorNode != null && conveyorNode.isTextual()) {
                    conveyor = conveyorNode.asText().trim();
                }
                if (robotNode != null && robotNode.isTextual()) {
                    robot = robotNode.asText().trim();
                }
                if ((robot == null || robot.isBlank()) && conveyor != null) {
                    robot = conveyor;
                }
            }

            if (stationId == null || stationId.isBlank() || conveyor == null || conveyor.isBlank()) {
                logger.warn("Ignoring malformed station binding for key '{}' in {}", stationId, sourceName);
                return;
            }

            stationBindings.put(normalizeStationId(stationId), new StationBinding(conveyor, robot));
        });
    }

    private String normalizeStationId(String stationId) {
        if (stationId == null) {
            return "";
        }
        return stationId.trim().toLowerCase(Locale.ROOT);
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

    private void publishReply(String stationId, String operation, String requestId, boolean success, String message) {
        if (replyTopicPrefix == null || replyTopicPrefix.isBlank()) {
            return;
        }

        try {
            ObjectNode reply = mapper.createObjectNode();
            reply.put("operation", operation);
            reply.put("requestId", requestId == null ? "" : requestId);
            reply.put("success", success);
            reply.put("message", message == null ? "" : message);

            String topic;
            if (stationId == null || stationId.isBlank()) {
                topic = replyTopicPrefix + "/" + operation;
            } else {
                topic = replyTopicPrefix + "/" + sanitizeTopicPart(stationId) + "/" + operation;
            }
            mqttClient.publish(topic, new MqttMessage(reply.toString().getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            logger.warn("Failed to publish MQTT reply", e);
        }
    }

    private String sanitizeTopicPart(String value) {
        if (value == null || value.isBlank()) {
            return "unknown";
        }
        return value.trim().replace('/', '_').replace('+', '_').replace('#', '_');
    }

    private static final class TopicRoute {
        private final String stationId;
        private final String action;

        private TopicRoute(String stationId, String action) {
            this.stationId = stationId;
            this.action = action;
        }
    }

    private static final class StationBinding {
        private final String conveyorSubmodelB64;
        private final String robotSubmodelB64;

        private StationBinding(String conveyorSubmodelB64, String robotSubmodelB64) {
            this.conveyorSubmodelB64 = conveyorSubmodelB64;
            this.robotSubmodelB64 = robotSubmodelB64;
        }
    }
}
