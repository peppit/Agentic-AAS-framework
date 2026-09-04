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
    private static final String SOURCE_TRANSFER_LOCATION =
            "urn:agent-aas:semantics:SourceTransferLocation:1";
    private static final String TARGET_TRANSFER_LOCATION =
            "urn:agent-aas:semantics:TargetTransferLocation:1";

    private final MqttCommandPublisherService mqttPublisher;

    public SimulationMachineOperationController(MqttCommandPublisherService mqttPublisher) {
        this.mqttPublisher = mqttPublisher;
    }

        @PostMapping(
            value = {"/simulation/stations/{stationId}/conveyorbelt/run"},
            produces = MediaType.APPLICATION_JSON_VALUE)
        public ResponseEntity<Map<String, Object>> setConveyorRunning(
            @RequestBody String input,
            @PathVariable(value = "stationId", required = false) String stationIdFromPath) {
        logger.info("Executing conveyor running operation");

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
            value = "/simulation/robots/{robotId}/movebox",
            produces = MediaType.APPLICATION_JSON_VALUE)
        public ResponseEntity<Map<String, Object>> moveBox(
            @RequestBody String input,
            @PathVariable("robotId") String robotId) {
        logger.info("Executing robot MoveBox operation");

        try {
            JsonObject root = parseInputRoot(input);
            String requestId = extractRequestId(root, input);
            String runId = extractStringParameter(root, "runId", "");
            JsonObject extractedParams = extractParams(root);
            String sourceIdentity = extractStringParameterBySemanticId(
                    root, SOURCE_TRANSFER_LOCATION);
            if (sourceIdentity == null || sourceIdentity.isBlank()) {
                sourceIdentity = extractStringParameterAny(root, null, "SourcePosition");
            }
            if (sourceIdentity == null || sourceIdentity.isBlank()) {
                sourceIdentity = extractStringFromParams(extractedParams, "SourcePosition");
            }

            String targetIdentity = extractStringParameterBySemanticId(
                    root, TARGET_TRANSFER_LOCATION);
            if (targetIdentity == null || targetIdentity.isBlank()) {
                targetIdentity = extractStringParameterAny(root, null, "TargetPosition");
            }
            if (targetIdentity == null || targetIdentity.isBlank()) {
                targetIdentity = extractStringFromParams(extractedParams, "TargetPosition");
            }

            if (sourceIdentity == null || sourceIdentity.isBlank()) {
                throw new IllegalArgumentException(
                        "Missing semantic SourceTransferLocation parameter");
            }
            if (targetIdentity == null || targetIdentity.isBlank()) {
                throw new IllegalArgumentException(
                        "Missing semantic TargetTransferLocation parameter");
            }

            String sourcePosition = sourceIdentity;
            String targetPosition = targetIdentity;

            JsonObject params = new JsonObject();
            params.addProperty("SourcePosition", sourcePosition);
            params.addProperty("TargetPosition", targetPosition);

            String operation = "moveBox";
            logger.info(
                "Publishing {} for source={} target={}",
                operation,
                sourcePosition,
                targetPosition);
            String payload = buildGenericCommandPayload(
                requestId, runId, null, operation, params);
            mqttPublisher.publishRobotOperation(robotId, operation, payload);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Robot MoveBox command published");
            response.put("requestId", requestId);
            response.put("runId", runId);
            response.put("operation", operation);
            response.put("SourcePosition", sourcePosition);
            response.put("TargetPosition", targetPosition);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing robot MoveBox operation", e);
            return buildErrorResponse("MoveBox", e);
        }
    }
        @PostMapping(
                value = {"/simulation/stations/{stationId}/robot/move-to-home"},
                produces = MediaType.APPLICATION_JSON_VALUE)
            public ResponseEntity<Map<String, Object>> moveToHome(
                @RequestBody String input,
                @PathVariable(value = "stationId", required = false) String stationIdFromPath) {
            logger.info("Executing robot move-to-home operation");

            try {
                JsonObject root = parseInputRoot(input);
                String requestId = extractRequestId(root, input);
                String stationId = extractRequiredStationId(root, stationIdFromPath);
                boolean move = parseBooleanInput(input, "move", false);

                String payload = String.format("{\"requestId\":\"%s\",\"value\":%s}", requestId, move);
                mqttPublisher.publishStationOperation(stationId, "MoveToHome", payload);

                Map<String, Object> response = new HashMap<>();
                response.put("status", "SUCCESS");
                response.put("message", "Robot move-to-home command published");
                response.put("requestId", requestId);
                response.put("stationId", stationId);
                response.put("move", move);
                return ResponseEntity.ok(response);
            } catch (Exception e) {
                logger.error("Error executing robot move-to-home operation", e);
                return buildErrorResponse("MoveToHome", e);
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

        return null;
    }

    private String extractStringParameterBySemanticId(
            JsonObject root, String expectedSemanticId) {
        JsonArray variables = findArgumentArray(root);
        if (variables == null) {
            return null;
        }
        for (JsonElement variable : variables) {
            if (!variable.isJsonObject()) {
                continue;
            }
            JsonObject wrapper = variable.getAsJsonObject();
            if (!wrapper.has("value") || !wrapper.get("value").isJsonObject()) {
                continue;
            }
            JsonObject value = wrapper.getAsJsonObject("value");
            if (hasSemanticId(value, expectedSemanticId)
                    && value.has("value")
                    && value.get("value").isJsonPrimitive()) {
                return value.get("value").getAsString();
            }
        }
        return null;
    }

    private boolean hasSemanticId(JsonObject element, String expectedSemanticId) {
        if (!element.has("semanticId") || !element.get("semanticId").isJsonObject()) {
            return false;
        }
        JsonObject semanticId = element.getAsJsonObject("semanticId");
        if (!semanticId.has("keys") || !semanticId.get("keys").isJsonArray()) {
            return false;
        }
        for (JsonElement keyElement : semanticId.getAsJsonArray("keys")) {
            if (!keyElement.isJsonObject()) {
                continue;
            }
            JsonObject key = keyElement.getAsJsonObject();
            if (key.has("value")
                    && expectedSemanticId.equals(key.get("value").getAsString())) {
                return true;
            }
        }
        return false;
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

    private JsonObject extractParams(JsonObject root) {
        JsonElement paramsElement = findInputValue(root, "params");
        if (paramsElement != null && paramsElement.isJsonObject()) {
            return paramsElement.getAsJsonObject();
        }

        JsonObject params = new JsonObject();
        JsonArray vars = findArgumentArray(root);

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

        JsonArray vars = findArgumentArray(root);
        if (vars == null) {
            return null;
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
            if (key.equalsIgnoreCase(valueObj.get("idShort").getAsString())) {
                return valueObj.get("value");
            }
        }

        return null;
    }

    private JsonArray findArgumentArray(JsonObject root) {
        if (root.has("inputVariables") && root.get("inputVariables").isJsonArray()) {
            return root.getAsJsonArray("inputVariables");
        }
        if (root.has("inputArguments") && root.get("inputArguments").isJsonArray()) {
            return root.getAsJsonArray("inputArguments");
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
        return buildGenericCommandPayload(
            requestId, "", stationId, operation, params);
    }

    private String buildGenericCommandPayload(
            String requestId,
            String runId,
            String stationId,
            String operation,
            JsonObject params) {
        JsonObject payload = new JsonObject();
        payload.addProperty("requestId", requestId);
        payload.addProperty("runId", runId);
        if (stationId != null && !stationId.isBlank()) {
            payload.addProperty("stationId", stationId);
        }
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
