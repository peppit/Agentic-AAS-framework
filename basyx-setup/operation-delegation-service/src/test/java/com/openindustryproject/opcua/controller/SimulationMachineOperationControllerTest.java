package com.openindustryproject.opcua.controller;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.openindustryproject.opcua.service.MqttCommandPublisherService;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.ResponseEntity;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class SimulationMachineOperationControllerTest {

    @Test
    void moveBoxPublishesPositionsWithoutStation() throws Exception {
        MqttCommandPublisherService publisher = mock(MqttCommandPublisherService.class);
        SimulationMachineOperationController controller =
                new SimulationMachineOperationController(publisher);
        String input = """
                {
                  "inputArguments": [
                    {"value":{"idShort":"SourcePosition","value":"Conveyor_A"}},
                    {"value":{"idShort":"TargetPosition","value":"Pallet_B"}},
                    {"value":{"idShort":"requestId","value":"request-1"}},
                    {"value":{"idShort":"runId","value":"run-1"}}
                  ]
                }
                """;

        ResponseEntity<Map<String, Object>> response = controller.moveBox(input, "Robot_02");

        assertEquals(200, response.getStatusCode().value());
        assertEquals("Conveyor_A", response.getBody().get("SourcePosition"));
        assertEquals("Pallet_B", response.getBody().get("TargetPosition"));

        ArgumentCaptor<String> payloadCaptor = ArgumentCaptor.forClass(String.class);
        verify(publisher).publishRobotOperation(
                org.mockito.ArgumentMatchers.eq("Robot_02"),
                org.mockito.ArgumentMatchers.eq("moveBox"),
                payloadCaptor.capture());

        JsonObject payload = JsonParser.parseString(payloadCaptor.getValue()).getAsJsonObject();
        assertEquals(false, payload.has("stationId"));
        assertEquals("request-1", payload.get("requestId").getAsString());
        assertEquals("run-1", payload.get("runId").getAsString());
        assertEquals(
                "Conveyor_A",
                payload.getAsJsonObject("params").get("SourcePosition").getAsString());
        assertEquals(
                "Pallet_B",
                payload.getAsJsonObject("params").get("TargetPosition").getAsString());
    }

    @Test
    void moveBoxPreservesCanonicalIdentitiesUsingParameterSemantics() throws Exception {
        MqttCommandPublisherService publisher = mock(MqttCommandPublisherService.class);
        SimulationMachineOperationController controller =
                new SimulationMachineOperationController(publisher);
        String input = """
                {
                  "inputArguments": [
                    {"value":{
                      "idShort":"pickupLocation",
                      "value":"urn:test:conveyor",
                      "semanticId":{"keys":[{"value":"urn:agent-aas:semantics:SourceTransferLocation:1"}]}
                    }},
                    {"value":{
                      "idShort":"dropLocation",
                      "value":"urn:test:pallet",
                      "semanticId":{"keys":[{"value":"urn:agent-aas:semantics:TargetTransferLocation:1"}]}
                    }},
                    {"value":{"idShort":"requestId","value":"request-2"}}
                  ]
                }
                """;

        ResponseEntity<Map<String, Object>> response = controller.moveBox(input, "Robot_02");

        assertEquals(200, response.getStatusCode().value());
        assertEquals("urn:test:conveyor", response.getBody().get("SourcePosition"));
        assertEquals("urn:test:pallet", response.getBody().get("TargetPosition"));

        ArgumentCaptor<String> payloadCaptor = ArgumentCaptor.forClass(String.class);
        verify(publisher).publishRobotOperation(
                org.mockito.ArgumentMatchers.eq("Robot_02"),
                org.mockito.ArgumentMatchers.eq("moveBox"),
                payloadCaptor.capture());
        JsonObject payload = JsonParser.parseString(payloadCaptor.getValue()).getAsJsonObject();
        assertEquals(
                "urn:test:conveyor",
                payload.getAsJsonObject("params").get("SourcePosition").getAsString());
        assertEquals(
                "urn:test:pallet",
                payload.getAsJsonObject("params").get("TargetPosition").getAsString());
    }
}
