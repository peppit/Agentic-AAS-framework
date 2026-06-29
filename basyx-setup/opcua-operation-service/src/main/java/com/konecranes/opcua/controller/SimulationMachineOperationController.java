package com.konecranes.opcua.controller;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.konecranes.opcua.service.MqttCommandPublisherService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Delegated operation controller for simulation machine commands.
 *
 * This controller accepts BaSyx operation invocation payloads and forwards
 * machine commands to MQTT topics consumed by the simulation stack.
 */
@RestController
public class SimulationMachineOperationController {

    private static final Logger logger = LoggerFactory.getLogger(SimulationMachineOperationController.class);

    private final MqttCommandPublisherService mqttPublisher;

    public SimulationMachineOperationController(MqttCommandPublisherService mqttPublisher) {
        this.mqttPublisher = mqttPublisher;
    }

    @PostMapping(value = "/simulation/operation/invoke", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> invokeSimulationOperation(@RequestBody String input) {
        logger.info("Executing generic simulation operation");
        logger.debug("Input received: {}", input);

        try {
            JsonObject root = parseInputRoot(input);
            String requestId = extractRequestId(root, input);
            String stationId = extractStringParameter(root, "stationId", "Station_01");
            String operation = extractStringParameter(root, "operation", null);

            if (operation == null || operation.isBlank()) {
                throw new IllegalArgumentException("Missing required parameter: operation");
            }

            JsonObject params = extractParams(root);
            String payload = buildGenericCommandPayload(requestId, stationId, operation, params);
            mqttPublisher.publishStationOperation(stationId, operation, payload);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Simulation operation command published");
            response.put("requestId", requestId);
            response.put("stationId", stationId);
            response.put("operation", operation);
            response.put("params", params);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing generic simulation operation", e);
            return buildErrorResponse("InvokeSimulationOperation", e);
        }
    }

    @PostMapping(value = "/simulation/conveyorbelt/running", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> setConveyorRunning(@RequestBody String input) {
        logger.info("Executing conveyor running operation");
        logger.debug("Input received: {}", input);

        try {
            boolean running = parseBooleanInput(input, "running", false);
            JsonObject root = parseInputRoot(input);
            String requestId = extractRequestId(root, input);

            String payload = String.format("{\"requestId\":\"%s\",\"value\":%s}", requestId, running);
            mqttPublisher.publishConveyorRunning(payload);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Conveyor running command published");
            response.put("requestId", requestId);
            response.put("running", running);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing conveyor running operation", e);
            return buildErrorResponse("SetConveyorRunning", e);
        }
    }

    @PostMapping(value = "/simulation/conveyorbelt/speed", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> setConveyorSpeed(@RequestBody String input) {
        logger.info("Executing conveyor speed operation");
        logger.debug("Input received: {}", input);

        try {
            double speed = parseDoubleInput(input, "speed", 0.0);
            JsonObject root = parseInputRoot(input);
            String requestId = extractRequestId(root, input);

            String payload = String.format("{\"requestId\":\"%s\",\"value\":%s}", requestId, speed);
            mqttPublisher.publishConveyorSpeed(payload);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Conveyor speed command published");
            response.put("requestId", requestId);
            response.put("speed", speed);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing conveyor speed operation", e);
            return buildErrorResponse("SetConveyorSpeed", e);
        }
    }

    private ResponseEntity<Map<String, Object>> buildErrorResponse(String operationName, Exception e) {
        Map<String, Object> errorResponse = new HashMap<>();
        errorResponse.put("status", "ERROR");
        errorResponse.put("error", operationName + " failed: " + e.getMessage());
        return ResponseEntity.internalServerError().body(errorResponse);
    }

    private String extractRequestId(JsonObject root, String input) {
        JsonElement requestIdElement = findInputValue(root, "requestId");
        if (requestIdElement != null && requestIdElement.isJsonPrimitive()) {
            return requestIdElement.getAsString();
        }

        try {
            JsonElement element = JsonParser.parseString(input);
            if (element.isJsonObject()) {
                JsonObject json = element.getAsJsonObject();
                if (json.has("requestId") && !json.get("requestId").isJsonNull()) {
                    return json.get("requestId").getAsString();
                }
            }
        } catch (Exception ignored) {
        }

        return UUID.randomUUID().toString();
    }

    private JsonObject parseInputRoot(String input) {
        JsonElement element = JsonParser.parseString(input);
        if (element.isJsonObject()) {
            return element.getAsJsonObject();
        }

        JsonObject root = new JsonObject();
        if (element.isJsonArray()) {
            root.add("inputVariables", element.getAsJsonArray());
        } else {
            root.add("value", element);
        }
        return root;
    }

    private String extractStringParameter(JsonObject root, String key, String defaultValue) {
        JsonElement element = findInputValue(root, key);
        if (element == null || element.isJsonNull()) {
            return defaultValue;
        }

        if (element.isJsonPrimitive()) {
            return element.getAsString();
        }

        return defaultValue;
    }

    private JsonObject extractParams(JsonObject root) {
        JsonElement paramsElement = findInputValue(root, "params");
        if (paramsElement != null && paramsElement.isJsonObject()) {
            return paramsElement.getAsJsonObject();
        }

        JsonObject params = new JsonObject();
        JsonArray vars = null;
        if (root.has("inputVariables") && root.get("inputVariables").isJsonArray()) {
            vars = root.getAsJsonArray("inputVariables");
        }

        if (vars == null) {
            return params;
        }

        for (JsonElement elem : vars) {
            if (!elem.isJsonObject()) {
                continue;
            }
            JsonObject varObj = elem.getAsJsonObject();
            if (!varObj.has("value") || !varObj.get("value").isJsonObject()) {
                continue;
            }

            JsonObject valueObj = varObj.getAsJsonObject("value");
            if (!valueObj.has("idShort") || !valueObj.has("value")) {
                continue;
            }

            String idShort = valueObj.get("idShort").getAsString();
            if ("stationId".equalsIgnoreCase(idShort)
                    || "operation".equalsIgnoreCase(idShort)
                    || "requestId".equalsIgnoreCase(idShort)
                    || "params".equalsIgnoreCase(idShort)) {
                continue;
            }

            params.add(idShort, coercePrimitive(valueObj.get("value")));
        }

        return params;
    }

    private JsonElement findInputValue(JsonObject root, String key) {
        if (root.has(key)) {
            return root.get(key);
        }

        if (!root.has("inputVariables") || !root.get("inputVariables").isJsonArray()) {
            return null;
        }

        JsonArray vars = root.getAsJsonArray("inputVariables");
        for (JsonElement elem : vars) {
            if (!elem.isJsonObject()) {
                continue;
            }
            JsonObject varObj = elem.getAsJsonObject();
            if (!varObj.has("value") || !varObj.get("value").isJsonObject()) {
                continue;
            }
            JsonObject valueObj = varObj.getAsJsonObject("value");
            if (!valueObj.has("idShort") || !valueObj.has("value")) {
                continue;
            }
            if (key.equalsIgnoreCase(valueObj.get("idShort").getAsString())) {
                return valueObj.get("value");
            }
        }

        return null;
    }

    private JsonElement coercePrimitive(JsonElement rawValue) {
        if (rawValue == null || rawValue.isJsonNull()) {
            return rawValue;
        }

        if (!rawValue.isJsonPrimitive()) {
            return rawValue;
        }

        if (rawValue.getAsJsonPrimitive().isBoolean() || rawValue.getAsJsonPrimitive().isNumber()) {
            return rawValue;
        }

        String text = rawValue.getAsString();
        String lower = text.trim().toLowerCase();
        if ("true".equals(lower) || "false".equals(lower)) {
            return JsonParser.parseString(lower);
        }

        try {
            double number = Double.parseDouble(text);
            if (Math.floor(number) == number) {
                return JsonParser.parseString(String.valueOf((long) number));
            }
            return JsonParser.parseString(String.valueOf(number));
        } catch (NumberFormatException ignored) {
            return rawValue;
        }
    }

    private String buildGenericCommandPayload(String requestId, String stationId, String operation, JsonObject params) {
        JsonObject payload = new JsonObject();
        payload.addProperty("requestId", requestId);
        payload.addProperty("stationId", stationId);
        payload.addProperty("operation", operation);
        payload.add("params", params == null ? new JsonObject() : params);
        return payload.toString();
    }

    private double parseDoubleInput(String input, String preferredKey, double defaultValue) {
        try {
            JsonElement element = JsonParser.parseString(input);

            if (element.isJsonObject()) {
                JsonObject json = element.getAsJsonObject();

                if (json.has(preferredKey)) {
                    return json.get(preferredKey).getAsDouble();
                }

                if (json.has("value")) {
                    JsonElement value = json.get("value");
                    if (value.isJsonPrimitive()) {
                        return value.getAsDouble();
                    }
                }

                if (json.has("inputVariables")) {
                    JsonArray inputVars = json.getAsJsonArray("inputVariables");
                    return findDoubleInVariables(inputVars, preferredKey, defaultValue);
                }
            }

            if (element.isJsonArray()) {
                return findDoubleInVariables(element.getAsJsonArray(), preferredKey, defaultValue);
            }

            return Double.parseDouble(input);
        } catch (Exception e) {
            throw new RuntimeException("Invalid numeric input", e);
        }
    }

    private boolean parseBooleanInput(String input, String preferredKey, boolean defaultValue) {
        try {
            JsonElement element = JsonParser.parseString(input);

            if (element.isJsonObject()) {
                JsonObject json = element.getAsJsonObject();

                if (json.has(preferredKey)) {
                    return parseBooleanElement(json.get(preferredKey));
                }

                if (json.has("value")) {
                    return parseBooleanElement(json.get("value"));
                }

                if (json.has("inputVariables")) {
                    JsonArray inputVars = json.getAsJsonArray("inputVariables");
                    return findBooleanInVariables(inputVars, preferredKey, defaultValue);
                }
            }

            if (element.isJsonArray()) {
                return findBooleanInVariables(element.getAsJsonArray(), preferredKey, defaultValue);
            }

            return parseBooleanLiteral(input);
        } catch (Exception e) {
            throw new RuntimeException("Invalid boolean input", e);
        }
    }

    private double findDoubleInVariables(JsonArray vars, String preferredKey, double defaultValue) {
        for (JsonElement elem : vars) {
            if (!elem.isJsonObject()) {
                continue;
            }
            JsonObject varObj = elem.getAsJsonObject();
            if (!varObj.has("value") || !varObj.get("value").isJsonObject()) {
                continue;
            }
            JsonObject valueObj = varObj.getAsJsonObject("value");
            if (valueObj.has("idShort") && preferredKey.equalsIgnoreCase(valueObj.get("idShort").getAsString())
                    && valueObj.has("value")) {
                return valueObj.get("value").getAsDouble();
            }
        }

        for (JsonElement elem : vars) {
            if (!elem.isJsonObject()) {
                continue;
            }
            JsonObject varObj = elem.getAsJsonObject();
            if (!varObj.has("value") || !varObj.get("value").isJsonObject()) {
                continue;
            }
            JsonObject valueObj = varObj.getAsJsonObject("value");
            if (valueObj.has("value")) {
                return valueObj.get("value").getAsDouble();
            }
        }

        return defaultValue;
    }

    private boolean findBooleanInVariables(JsonArray vars, String preferredKey, boolean defaultValue) {
        for (JsonElement elem : vars) {
            if (!elem.isJsonObject()) {
                continue;
            }
            JsonObject varObj = elem.getAsJsonObject();
            if (!varObj.has("value") || !varObj.get("value").isJsonObject()) {
                continue;
            }
            JsonObject valueObj = varObj.getAsJsonObject("value");
            if (valueObj.has("idShort") && preferredKey.equalsIgnoreCase(valueObj.get("idShort").getAsString())
                    && valueObj.has("value")) {
                return parseBooleanElement(valueObj.get("value"));
            }
        }

        for (JsonElement elem : vars) {
            if (!elem.isJsonObject()) {
                continue;
            }
            JsonObject varObj = elem.getAsJsonObject();
            if (!varObj.has("value") || !varObj.get("value").isJsonObject()) {
                continue;
            }
            JsonObject valueObj = varObj.getAsJsonObject("value");
            if (valueObj.has("value")) {
                return parseBooleanElement(valueObj.get("value"));
            }
        }

        return defaultValue;
    }

    private boolean parseBooleanElement(JsonElement element) {
        if (element == null || element.isJsonNull()) {
            return false;
        }
        if (element.getAsJsonPrimitive().isBoolean()) {
            return element.getAsBoolean();
        }
        if (element.getAsJsonPrimitive().isNumber()) {
            return element.getAsInt() != 0;
        }
        return parseBooleanLiteral(element.getAsString());
    }

    private boolean parseBooleanLiteral(String raw) {
        String normalized = raw == null ? "" : raw.trim().toLowerCase();
        return "true".equals(normalized) || "1".equals(normalized) || "on".equals(normalized);
    }
}
