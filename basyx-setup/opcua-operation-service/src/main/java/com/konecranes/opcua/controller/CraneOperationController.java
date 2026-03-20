package com.konecranes.opcua.controller;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.konecranes.opcua.service.OpcUaService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * REST controller for crane operations delegated from AAS
 * Implements the BaSyx Operation Delegation pattern
 */
@RestController
public class CraneOperationController {

    private static final Logger logger = LoggerFactory.getLogger(CraneOperationController.class);

    // OPC UA Node IDs for crane controls
    private static final String HOIST_DOWN_NODE = "ns=5;s=DX_Custom_V.Controls.Hoist.Down";
    private static final String HOIST_UP_NODE = "ns=5;s=DX_Custom_V.Controls.Hoist.Up";
    private static final String TROLLEY_FORWARD_NODE = "ns=5;s=DX_Custom_V.Controls.Trolley.Forward";
    private static final String TROLLEY_BACKWARD_NODE = "ns=5;s=DX_Custom_V.Controls.Trolley.Backward";
    private static final String BRIDGE_FORWARD_NODE = "ns=5;s=DX_Custom_V.Controls.Bridge.Forward";
    private static final String BRIDGE_BACKWARD_NODE = "ns=5;s=DX_Custom_V.Controls.Bridge.Backward";

    // Speed control node IDs
    private static final String HOIST_SPEED_NODE = "ns=5;s=DX_Custom_V.Controls.Hoist.Speed";
    private static final String TROLLEY_SPEED_NODE = "ns=5;s=DX_Custom_V.Controls.Trolley.Speed";
    private static final String BRIDGE_SPEED_NODE = "ns=5;s=DX_Custom_V.Controls.Bridge.Speed";
    private static final String WATCHDOG_NODE = "ns=5;s=DX_Custom_V.Controls.Watchdog";
    private static final String ACCESS_CODE_NODE = "ns=5;s=DX_Custom_V.Controls.AccessCode";

    // Position feedback node IDs
    private static final String HOIST_POSITION_MM_NODE = "ns=5;s=DX_Custom_V.Status.Hoist.Position.Position_mm";
    private static final String TROLLEY_POSITION_MM_NODE = "ns=5;s=DX_Custom_V.Status.Trolley.Position.Position_mm";
    private static final String BRIDGE_POSITION_MM_NODE = "ns=5;s=DX_Custom_V.Status.Bridge.Position.Position_mm";


    private static final long PULSE_DURATION_MS = 2500; // 3 seconds
    private static final double DEFAULT_TOLERANCE_MM = 2.0;
    private static final long DEFAULT_TIMEOUT_MS = 120000;
    private static final long CONTROL_LOOP_SLEEP_MS = 100;
    private static final int WATCHDOG_MAX = 1000000;

    @Autowired
    private OpcUaService opcUaService;

    @Value("${opcua.access-code:}")
    private String accessCode;

    @Value("${opcua.watchdog.increment-enabled:false}")
    private boolean watchdogIncrementEnabled;

    @Value("${opcua.watchdog.require-active:false}")
    private boolean watchdogRequireActive;

    @Value("${opcua.watchdog.max-stale-ms:5000}")
    private long watchdogMaxStaleMs;

    private Long lastWatchdogValue;
    private long lastWatchdogChangeTimestamp;
    private final AtomicBoolean hasLoggedMissingAccessCode = new AtomicBoolean(false);

    private static class OperationPreconditionException extends RuntimeException {
        OperationPreconditionException(String message) {
            super(message);
        }
    }

    /**
     * SetHoistSpeed operation - sets hoist speed for crane controls
     * Input: Speed value (0-100) as double
     * Output: Success status (boolean)
     */
    @PostMapping(value = "/crane/set-hoist-speed", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> setHoistSpeed(@RequestBody String input) {
        logger.info("Executing SetHoistSpeed operation");
        logger.debug("Input received: {}", input);

        try {
            authorizeAccessIfConfigured();
            double speed = Double.parseDouble(input.trim());
            if (speed < 0 || speed > 100) {
                throw new IllegalArgumentException("Speed must be between 0 and 100");
            }
            opcUaService.writeDouble(HOIST_SPEED_NODE, speed);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Hoist speed set successfully");
            response.put("speed", speed);
            response.put("success", true);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing SetHoistSpeed", e);
            return buildErrorResponse("SetHoistSpeed", e);
        }
    }

    /**
     * SetTrolleySpeed operation - sets trolley speed for crane controls
     * Input: Speed value (0-100) as double
     * Output: Success status (boolean)
     */
    @PostMapping(value = "/crane/set-trolley-speed", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> setTrolleySpeed(@RequestBody String input) {
        logger.info("Executing SetTrolleySpeed operation");
        logger.debug("Input received: {}", input);

        try {
            authorizeAccessIfConfigured();
            double speed = Double.parseDouble(input.trim());
            if (speed < 0 || speed > 100) {
                throw new IllegalArgumentException("Speed must be between 0 and 100");
            }
            opcUaService.writeDouble(TROLLEY_SPEED_NODE, speed);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Trolley speed set successfully");
            response.put("speed", speed);
            response.put("success", true);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing SetTrolleySpeed", e);
            return buildErrorResponse("SetTrolleySpeed", e);
        }
    }

    /** 
     * Bridge speed control operation - sets bridge speed for crane controls
     * Input: Speed value (0-100) as double
     * Output: Success status (boolean)
     */
    @PostMapping(value = "/crane/set-bridge-speed", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> setBridgeSpeed(@RequestBody String input) {
        logger.info("Executing SetBridgeSpeed operation");
        logger.debug("Input received: {}", input);

        try {
            authorizeAccessIfConfigured();
            double speed = Double.parseDouble(input.trim());
            if (speed < 0 || speed > 100) {
                throw new IllegalArgumentException("Speed must be between 0 and 100");
            }
            opcUaService.writeDouble(BRIDGE_SPEED_NODE, speed);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "Bridge speed set successfully");
            response.put("speed", speed);
            response.put("success", true);
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing SetBridgeSpeed", e);
            return buildErrorResponse("SetBridgeSpeed", e);
        }
    }


    /**
     * Hoist Down operation - activates the hoist down control
     * Input: Optional duration parameter (default 10000ms)
     * Output: Success status message
     */
    @PostMapping(value = "/crane/hoist-down", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> hoistDown(@RequestBody(required = false) String input) {
        logger.info("Executing HoistDown operation");

        try {
            authorizeAccessIfConfigured();
            opcUaService.writePulse(HOIST_DOWN_NODE, PULSE_DURATION_MS);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "HoistDown executed successfully");
            response.put("duration_ms", PULSE_DURATION_MS);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing HoistDown", e);
            return buildErrorResponse("HoistDown", e);
        }
    }

    /**
     * Hoist Up operation - activates the hoist up control
     * Input: Optional duration parameter (default 10000ms)
     * Output: Success status message
     */
    @PostMapping(value = "/crane/hoist-up", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> hoistUp(@RequestBody(required = false) String input) {
        logger.info("Executing HoistUp operation");

        try {
            authorizeAccessIfConfigured();
            opcUaService.writePulse(HOIST_UP_NODE, PULSE_DURATION_MS);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "HoistUp executed successfully");
            response.put("duration_ms", PULSE_DURATION_MS);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing HoistUp", e);
            return buildErrorResponse("HoistUp", e);
        }
    }

    /**
     * Trolley Forward operation - moves trolley forward
     * Input: Optional duration parameter (default 10000ms)
     * Output: Success status message
     */
    @PostMapping(value = "/crane/trolley-forward", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> trolleyForward(@RequestBody(required = false) String input) {
        logger.info("Executing TrolleyForward operation");

        try {
            authorizeAccessIfConfigured();
            opcUaService.writePulse(TROLLEY_FORWARD_NODE, PULSE_DURATION_MS);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "TrolleyForward executed successfully");
            response.put("duration_ms", PULSE_DURATION_MS);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing TrolleyForward", e);
            return buildErrorResponse("TrolleyForward", e);
        }
    }

    /**
     * Trolley Backward operation - moves trolley backward
     * Input: Optional duration parameter (default 10000ms)
     * Output: Success status message
     */
    @PostMapping(value = "/crane/trolley-backward", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> trolleyBackward(@RequestBody(required = false) String input) {
        logger.info("Executing TrolleyBackward operation");

        try {
            authorizeAccessIfConfigured();
            opcUaService.writePulse(TROLLEY_BACKWARD_NODE, PULSE_DURATION_MS);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "TrolleyBackward executed successfully");
            response.put("duration_ms", PULSE_DURATION_MS);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing TrolleyBackward", e);
            return buildErrorResponse("TrolleyBackward", e);
        }
    }

    /**
     * Bridge Forward operation - moves bridge forward
     * Input: Optional duration parameter (default 10000ms)
     * Output: Success status message
     */
    @PostMapping(value = "/crane/bridge-forward", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> bridgeForward(@RequestBody(required = false) String input) {
        logger.info("Executing BridgeForward operation");

        try {
            authorizeAccessIfConfigured();
            opcUaService.writePulse(BRIDGE_FORWARD_NODE, PULSE_DURATION_MS);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "BridgeForward executed successfully");
            response.put("duration_ms", PULSE_DURATION_MS);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing BridgeForward", e);
            return buildErrorResponse("BridgeForward", e);
        }
    }

    /**
     * Bridge Backward operation - moves bridge backward
     * Input: Optional duration parameter (default 10000ms)
     * Output: Success status message
     */
    @PostMapping(value = "/crane/bridge-backward", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> bridgeBackward(@RequestBody(required = false) String input) {
        logger.info("Executing BridgeBackward operation");

        try {
            authorizeAccessIfConfigured();
            opcUaService.writePulse(BRIDGE_BACKWARD_NODE, PULSE_DURATION_MS);

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "BridgeBackward executed successfully");
            response.put("duration_ms", PULSE_DURATION_MS);

            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing BridgeBackward", e);
            return buildErrorResponse("BridgeBackward", e);
        }
    }

    /**
     * DriveToTarget operation - moves crane to specified coordinates
     * Input: Bridge (x), Trolley (y), Hoist (z) positions as doubles
     * Output: Success status message
     */
    @PostMapping(value = "/crane/drive-to-target", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> driveToTarget(@RequestBody String input) {
        logger.info("Executing DriveToTarget operation");
        logger.debug("Input received: {}", input);
        long startTime = System.currentTimeMillis();

        try {
            authorizeAccessIfConfigured();
            // Parse input parameters from BaSyx JSON
            Map<String, Double> params = parseOperationInput(input);
            double bridge = params.getOrDefault("Bridge", 0.0);
            double trolley = params.getOrDefault("Trolley", 0.0);
            double hoist = params.getOrDefault("Hoist", 0.0);
            double toleranceMm = params.getOrDefault("ToleranceMm", DEFAULT_TOLERANCE_MM);
            long timeoutMs = Math.max(1000L, params.getOrDefault("TimeoutMs", (double) DEFAULT_TIMEOUT_MS).longValue());
            boolean fast = params.getOrDefault("Fast", 0.0) > 0.5;

            logger.info("Target position: Bridge={}, Trolley={}, Hoist={}, tolerance={}mm, timeout={}ms, fast={}",
                    bridge, trolley, hoist, toleranceMm, timeoutMs, fast);

            while (true) {
                long elapsed = System.currentTimeMillis() - startTime;
                if (elapsed > timeoutMs) {
                    stopAllAxes();
                    Map<String, Object> timeoutResponse = new HashMap<>();
                    timeoutResponse.put("status", "TIMEOUT");
                    timeoutResponse.put("message", "DriveToTarget timed out before reaching target");
                    timeoutResponse.put("duration_ms", elapsed);
                    timeoutResponse.put("finalBridge", readPositionMm(BRIDGE_POSITION_MM_NODE));
                    timeoutResponse.put("finalTrolley", readPositionMm(TROLLEY_POSITION_MM_NODE));
                    timeoutResponse.put("finalHoist", readPositionMm(HOIST_POSITION_MM_NODE));
                    return ResponseEntity.status(HttpStatus.REQUEST_TIMEOUT).body(timeoutResponse);
                }

                double currentBridge = readPositionMm(BRIDGE_POSITION_MM_NODE);
                double currentTrolley = readPositionMm(TROLLEY_POSITION_MM_NODE);
                double currentHoist = readPositionMm(HOIST_POSITION_MM_NODE);

                boolean bridgeDone = controlBridgeAxis(bridge, currentBridge, toleranceMm, fast);
                boolean trolleyDone = controlTrolleyAxis(trolley, currentTrolley, toleranceMm, fast);
                boolean hoistDone = controlHoistAxis(hoist, currentHoist, toleranceMm, fast);

                incrementWatchdogSafe();

                if (bridgeDone && trolleyDone && hoistDone) {
                    stopAllAxes();

                    Map<String, Object> response = new HashMap<>();
                    response.put("status", "SUCCESS");
                    response.put("message", String.format("Reached target (Bridge=%.2f, Trolley=%.2f, Hoist=%.2f)",
                            bridge, trolley, hoist));
                    response.put("duration_ms", elapsed);
                    response.put("finalBridge", currentBridge);
                    response.put("finalTrolley", currentTrolley);
                    response.put("finalHoist", currentHoist);

                    return ResponseEntity.ok(response);
                }

                Thread.sleep(CONTROL_LOOP_SLEEP_MS);
            }

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            stopAllAxesQuietly();
            logger.error("DriveToTarget interrupted", e);
            Map<String, Object> errorResponse = new HashMap<>();
            errorResponse.put("status", "ERROR");
            errorResponse.put("error", "DriveToTarget interrupted: " + e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(errorResponse);
        } catch (Exception e) {
            stopAllAxesQuietly();
            logger.error("Error executing DriveToTarget", e);
            return buildErrorResponse("DriveToTarget", e);
        }
    }

    @PostMapping(value = {"/crane/stop-all", "/crane/stopAll"}, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Map<String, Object>> stopAll(@RequestBody(required = false) String input) {
        logger.info("Executing StopAll operation");

        try {
            authorizeAccessIfConfigured();
            stopAllAxes();

            Map<String, Object> response = new HashMap<>();
            response.put("status", "SUCCESS");
            response.put("message", "All axes stopped");
            return ResponseEntity.ok(response);
        } catch (Exception e) {
            logger.error("Error executing StopAll", e);
            return buildErrorResponse("StopAll", e);
        }
    }

    private ResponseEntity<Map<String, Object>> buildErrorResponse(String operationName, Exception e) {
        Map<String, Object> errorResponse = new HashMap<>();
        errorResponse.put("status", "ERROR");
        errorResponse.put("error", operationName + " failed: " + e.getMessage());

        HttpStatus status = (e instanceof OperationPreconditionException)
                ? HttpStatus.SERVICE_UNAVAILABLE
                : HttpStatus.INTERNAL_SERVER_ERROR;

        return ResponseEntity.status(status).body(errorResponse);
    }

    private boolean controlBridgeAxis(double target, double current, double toleranceMm, boolean fast) throws Exception {
        double error = current - target;
        if (Math.abs(error) <= toleranceMm) {
            opcUaService.writeDouble(BRIDGE_SPEED_NODE, 0.0);
            opcUaService.writeBoolean(BRIDGE_FORWARD_NODE, false);
            opcUaService.writeBoolean(BRIDGE_BACKWARD_NODE, false);
            return true;
        }

        double speed = rampHorizontal(error, fast);
        if (error > 0) {
            opcUaService.writeBoolean(BRIDGE_FORWARD_NODE, false);
            opcUaService.writeBoolean(BRIDGE_BACKWARD_NODE, true);
        } else {
            opcUaService.writeBoolean(BRIDGE_BACKWARD_NODE, false);
            opcUaService.writeBoolean(BRIDGE_FORWARD_NODE, true);
        }
        opcUaService.writeDouble(BRIDGE_SPEED_NODE, speed);
        return false;
    }

    private boolean controlTrolleyAxis(double target, double current, double toleranceMm, boolean fast) throws Exception {
        double error = current - target;
        if (Math.abs(error) <= toleranceMm) {
            opcUaService.writeDouble(TROLLEY_SPEED_NODE, 0.0);
            opcUaService.writeBoolean(TROLLEY_FORWARD_NODE, false);
            opcUaService.writeBoolean(TROLLEY_BACKWARD_NODE, false);
            return true;
        }

        double speed = rampHorizontal(error, fast);
        if (error > 0) {
            opcUaService.writeBoolean(TROLLEY_FORWARD_NODE, false);
            opcUaService.writeBoolean(TROLLEY_BACKWARD_NODE, true);
        } else {
            opcUaService.writeBoolean(TROLLEY_BACKWARD_NODE, false);
            opcUaService.writeBoolean(TROLLEY_FORWARD_NODE, true);
        }
        opcUaService.writeDouble(TROLLEY_SPEED_NODE, speed);
        return false;
    }

    private boolean controlHoistAxis(double target, double current, double toleranceMm, boolean fast) throws Exception {
        double error = current - target;
        if (Math.abs(error) <= toleranceMm) {
            opcUaService.writeDouble(HOIST_SPEED_NODE, 0.0);
            opcUaService.writeBoolean(HOIST_UP_NODE, false);
            opcUaService.writeBoolean(HOIST_DOWN_NODE, false);
            return true;
        }

        double speed = fast ? rampLift(error) : rampLower(error);
        if (error > 0) {
            opcUaService.writeBoolean(HOIST_UP_NODE, false);
            opcUaService.writeBoolean(HOIST_DOWN_NODE, true);
        } else {
            opcUaService.writeBoolean(HOIST_DOWN_NODE, false);
            opcUaService.writeBoolean(HOIST_UP_NODE, true);
        }
        opcUaService.writeDouble(HOIST_SPEED_NODE, speed);
        return false;
    }

    private double rampHorizontal(double error, boolean fast) {
        double abs = Math.abs(error);
        double speed;

        if (abs < 15) {
            speed = 0.6;
        } else if (abs < 2000) {
            speed = abs / 20.0;
        } else {
            speed = 100.0;
        }

        if (fast && speed < 15.0) {
            speed = 15.0;
        }

        return clamp(speed, 0.0, 100.0);
    }

    private double rampLower(double error) {
        double abs = Math.abs(error);
        double speed;

        if (abs < 8) {
            speed = 2.0;
        } else if (abs < 400) {
            speed = abs / 4.0;
        } else {
            speed = 100.0;
        }

        return clamp(speed, 0.0, 100.0);
    }

    private double rampLift(double error) {
        double abs = Math.abs(error);
        double speed;

        if (abs < 8) {
            speed = 4.0;
        } else if (abs < 200) {
            speed = abs / 2.0;
        } else {
            speed = 100.0;
        }

        return clamp(speed, 0.0, 100.0);
    }

    private double clamp(double value, double min, double max) {
        return Math.max(min, Math.min(max, value));
    }

    private double readPositionMm(String nodeId) throws Exception {
        return opcUaService.readDouble(nodeId);
    }

    private void stopAllAxes() throws Exception {
        opcUaService.writeDouble(BRIDGE_SPEED_NODE, 0.0);
        opcUaService.writeDouble(TROLLEY_SPEED_NODE, 0.0);
        opcUaService.writeDouble(HOIST_SPEED_NODE, 0.0);

        opcUaService.writeBoolean(BRIDGE_FORWARD_NODE, false);
        opcUaService.writeBoolean(BRIDGE_BACKWARD_NODE, false);
        opcUaService.writeBoolean(TROLLEY_FORWARD_NODE, false);
        opcUaService.writeBoolean(TROLLEY_BACKWARD_NODE, false);
        opcUaService.writeBoolean(HOIST_UP_NODE, false);
        opcUaService.writeBoolean(HOIST_DOWN_NODE, false);
    }

    private void stopAllAxesQuietly() {
        try {
            stopAllAxes();
        } catch (Exception ex) {
            logger.warn("Failed to stop all axes during error handling", ex);
        }
    }

    private void incrementWatchdogSafe() {
        if (!watchdogIncrementEnabled) {
            return;
        }

        try {
            long current = opcUaService.readLong(WATCHDOG_NODE);
            int next = (int) ((Math.max(0, current) % WATCHDOG_MAX) + 1);
            opcUaService.writeInt16(WATCHDOG_NODE, next);
        } catch (Exception ex) {
            logger.warn("Failed to increment watchdog", ex);
        }
    }

    private void authorizeAccessIfConfigured() throws Exception {
        ensureWatchdogActiveIfRequired();

        if (accessCode == null || accessCode.isBlank()) {
            if (hasLoggedMissingAccessCode.compareAndSet(false, true)) {
                logger.warn("opcua.access-code is not configured; continuing without explicit authorization write");
            }
            return;
        }

        int code;
        try {
            code = Integer.parseInt(accessCode.trim());
        } catch (NumberFormatException ex) {
            throw new IllegalStateException("Invalid opcua.access-code configuration; expected integer", ex);
        }

        try {
            opcUaService.writeInt32(ACCESS_CODE_NODE, code);
        } catch (Exception ex) {
            logger.warn("AccessCode write failed at node {}; continuing because external server may own authorization", ACCESS_CODE_NODE, ex);
        }
    }

    private synchronized void ensureWatchdogActiveIfRequired() throws Exception {
        if (!watchdogRequireActive) {
            return;
        }

        long current = opcUaService.readLong(WATCHDOG_NODE);
        long now = System.currentTimeMillis();

        if (lastWatchdogValue == null) {
            lastWatchdogValue = current;
            lastWatchdogChangeTimestamp = now;
            return;
        }

        if (current != lastWatchdogValue) {
            lastWatchdogValue = current;
            lastWatchdogChangeTimestamp = now;
            return;
        }

        long staleForMs = now - lastWatchdogChangeTimestamp;
        if (staleForMs > watchdogMaxStaleMs) {
            throw new OperationPreconditionException(
                    "Watchdog is not active (unchanged for " + staleForMs + " ms, limit " + watchdogMaxStaleMs + " ms)"
            );
        }
    }

    /**
     * Parse BaSyx operation input variables to extract parameters
     * BaSyx sends inputVariables as array of objects with structure:
     * { "value": { "idShort": "Bridge", "value": "200.0" } }
     */
    private Map<String, Double> parseOperationInput(String input) {
        Map<String, Double> params = new HashMap<>();

        try {
            JsonElement element = JsonParser.parseString(input);

            // Handle both array format and object format
            if (element.isJsonArray()) {
                // Direct array of input variables
                JsonArray inputVars = element.getAsJsonArray();

                for (JsonElement elem : inputVars) {
                    JsonObject varObj = elem.getAsJsonObject();
                    if (varObj.has("value")) {
                        JsonObject value = varObj.getAsJsonObject("value");
                        String idShort = value.get("idShort").getAsString();
                        String valueStr = value.get("value").getAsString();
                        double val = Double.parseDouble(valueStr);
                        params.put(idShort, val);
                    }
                }
            } else if (element.isJsonObject()) {
                JsonObject json = element.getAsJsonObject();

                // Check for inputVariables field
                if (json.has("inputVariables")) {
                    JsonArray inputVars = json.getAsJsonArray("inputVariables");

                    for (JsonElement elem : inputVars) {
                        JsonObject varObj = elem.getAsJsonObject();
                        if (varObj.has("value")) {
                            JsonObject value = varObj.getAsJsonObject("value");
                            String idShort = value.get("idShort").getAsString();
                            String valueStr = value.get("value").getAsString();
                            double val = Double.parseDouble(valueStr);
                            params.put(idShort, val);
                        }
                    }
                }
            }

            logger.debug("Parsed parameters: {}", params);
            return params;

        } catch (Exception e) {
            logger.error("Failed to parse input parameters: {}", input, e);
            throw new RuntimeException("Invalid input format: " + e.getMessage(), e);
        }
    }
}

