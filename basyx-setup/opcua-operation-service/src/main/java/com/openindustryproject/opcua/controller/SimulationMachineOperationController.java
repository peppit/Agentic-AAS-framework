package com.openindustryproject.opcua.controller;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.openindustryproject.opcua.service.MqttCommandPublisherService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
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

        @PostMapping(
            value = {"/simulation/operation/invoke", "/simulation/stations/{stationId}/operation/invoke"},
            produces = MediaType.APPLICATION_JSON_VALUE)
        public ResponseEntity<Map<String, Object>> invokeSimulationOperation(
            @RequestBody String input,
            @PathVariable(value = "stationId", required = false) String stationIdFromPath) {
        logger.info("Executing generic simulation operation");
        logger.debug("Input received: {}", input);

        try {
            JsonObject root = parseInputRoot(input);
            String requestId = extractRequestId(root, input);
            String stationId = extractRequiredStationId(root, stationIdFromPath);
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

        @PostMapping(
            value = {"/simulation/stations/{stationId}/conveyorbelt/run"},
            produces = MediaType.APPLICATION_JSON_VALUE)
        public ResponseEntity<Map<String, Object>> setConveyorRunning(
            @RequestBody String input,
            @PathVariable(value = "stationId", required = false) String stationIdFromPath) {
        logger.info("Executing conveyor running operation");
        logger.debug("Input received: {}", input);

        try {
            JsonObject root = parseInputRoot(input);
            String requestId = extractRequestId(root, input);
            String stationId = extractRequiredStationId(root, stationIdFromPath);
            boolean running = parseBooleanInput(input, "running", false);

            String payload = String.format("{\"requestId\":\"%s\",\"value\":%s}", requestId, running);
            mqttPublisher.publishStationOperation(stationId, "conveyorRunning", payload);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Conveyor running command published");
            response.put("requestId", requestId);
            response.put("stationId", stationId);
            response.put("running", running);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing conveyor running operation", e);
            return buildErrorResponse("SetConveyorRunning", e);
        }
    }

        @PostMapping(
            value = {"/simulation/stations/{stationId}/conveyorbelt/speed"},
            produces = MediaType.APPLICATION_JSON_VALUE)
        public ResponseEntity<Map<String, Object>> setConveyorSpeed(
            @RequestBody String input,
            @PathVariable(value = "stationId", required = false) String stationIdFromPath) {
        logger.info("Executing conveyor speed operation");
        logger.debug("Input received: {}", input);

        try {
            JsonObject root = parseInputRoot(input);
            String requestId = extractRequestId(root, input);
            String stationId = extractRequiredStationId(root, stationIdFromPath);
            double speed = parseDoubleInput(input, "speed", 0.0);

            String payload = String.format("{\"requestId\":\"%s\",\"value\":%s}", requestId, speed);
            mqttPublisher.publishStationOperation(stationId, "conveyorSpeed", payload);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Conveyor speed command published");
            response.put("requestId", requestId);
            response.put("stationId", stationId);
            response.put("speed", speed);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing conveyor speed operation", e);
            return buildErrorResponse("SetConveyorSpeed", e);
        }
    }

        @PostMapping(
            value = {"/simulation/stations/{stationId}/robot/movebox"},
            produces = MediaType.APPLICATION_JSON_VALUE)
        public ResponseEntity<Map<String, Object>> moveBox(
            @RequestBody String input,
            @PathVariable(value = "stationId", required = false) String stationIdFromPath) {
        logger.info("Executing robot MoveBox operation");
        logger.debug("Input received: {}", input);

        try {
            JsonObject root = parseInputRoot(input);
            String requestId = extractRequestId(root, input);
            String stationId = extractRequiredStationId(root, stationIdFromPath);
            JsonObject extractedParams = extractParams(root);
            String conveyor = extractStringParameterAny(root, null, "Conveyor1", "conveyor1", "Conveyor", "conveyor", "SourcePosition", "sourcePosition") ;
            if (conveyor == null || conveyor.isBlank()) {
                conveyor = extractStringFromParams(extractedParams, "Conveyor1", "conveyor1", "Conveyor", "conveyor", "SourcePosition", "sourcePosition", "SourcePosition.");
            }

            String pallet = extractStringParameterAny(root, null, "Pallet1", "pallet1", "Pallet", "pallet", "TargetPosition", "targetPosition") ;
            if (pallet == null || pallet.isBlank()) {
                pallet = extractStringFromParams(extractedParams, "Pallet1", "pallet1", "Pallet", "pallet", "TargetPosition", "targetPosition", "TargetPosition.");
            }

            if (conveyor == null || conveyor.isBlank()) {
                throw new IllegalArgumentException("Missing required parameter: Conveyor1");
            }
            if (pallet == null || pallet.isBlank()) {
                throw new IllegalArgumentException("Missing required parameter: Pallet1");
            }

            JsonObject params = new JsonObject();
            params.addProperty("Conveyor1", conveyor);
            params.addProperty("Pallet1", pallet);

            String operation = "moveBox";
            String payload = buildGenericCommandPayload(requestId, stationId, operation, params);
            mqttPublisher.publishStationOperation(stationId, operation, payload);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Robot MoveBox command published");
            response.put("requestId", requestId);
            response.put("stationId", stationId);
            response.put("operation", operation);
            response.put("Conveyor1", conveyor);
            response.put("Pallet1", pallet);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing robot MoveBox operation", e);
            return buildErrorResponse("MoveBox", e);
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

    private String extractStringParameterAny(JsonObject root, String defaultValue, String... keys) {
        for (String key : keys) {
            String value = extractStringParameter(root, key, null);
            if (value != null && !value.isBlank()) {
                return value;
            }
        }
        return defaultValue;
    }

    private String extractStringFromParams(JsonObject params, String... keys) {
        if (params == null) {
            return null;
        }

        for (String key : keys) {
            if (!params.has(key)) {
                continue;
            }

            JsonElement value = params.get(key);
            if (value == null || value.isJsonNull() || !value.isJsonPrimitive()) {
                continue;
            }

            String text = value.getAsString();
            if (text != null && !text.isBlank()) {
                return text;
            }
        }

        // Fallback for accidental punctuation differences like TargetPosition.
        JsonObject normalizedAliasMap = new JsonObject();
        for (String key : keys) {
            normalizedAliasMap.addProperty(normalizeIdShort(key), "1");
        }

        for (Map.Entry<String, JsonElement> entry : params.entrySet()) {
            String normalized = normalizeIdShort(entry.getKey());
            if (!normalizedAliasMap.has(normalized)) {
                continue;
            }

            JsonElement value = entry.getValue();
            if (value == null || value.isJsonNull() || !value.isJsonPrimitive()) {
                continue;
            }

            String text = value.getAsString();
            if (text != null && !text.isBlank()) {
                return text;
            }
        }

        return null;
    }

    private String extractRequiredStationId(JsonObject root, String stationIdFromPath) {
        if (stationIdFromPath != null && !stationIdFromPath.isBlank()) {
            return stationIdFromPath;
        }

        String stationId = extractStringParameterAny(root, null, "stationId", "StationId");
        if (stationId != null && !stationId.isBlank()) {
            return stationId;
        }

        JsonObject params = extractParams(root);
        stationId = extractStringFromParams(params, "stationId", "StationId");
        if (stationId != null && !stationId.isBlank()) {
            return stationId;
        }

        throw new IllegalArgumentException("Missing required parameter: stationId");
    }

    private String normalizeIdShort(String raw) {
        if (raw == null) {
            return "";
        }

        StringBuilder normalized = new StringBuilder();
        for (char c : raw.toCharArray()) {
            if (Character.isLetterOrDigit(c)) {
                normalized.append(Character.toLowerCase(c));
            }
        }
        return normalized.toString();
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
