package com.openindustryproject.opcua.controller;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.openindustryproject.opcua.service.MqttCommandPublisherService;
import com.openindustryproject.opcua.service.SimulationIdentityTranslator;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.ResponseEntity;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class SimulationMachineOperationControllerTest {

    @Test
    void moveBoxUsesInputStationAndSeparatePositions() throws Exception {
        MqttCommandPublisherService publisher = mock(MqttCommandPublisherService.class);
        SimulationMachineOperationController controller =
                new SimulationMachineOperationController(publisher);
        String input = """
                {
                  "inputArguments": [
                    {"value":{"idShort":"StationId","value":"Station_01"}},
                    {"value":{"idShort":"SourcePosition","value":"Conveyor_A"}},
                    {"value":{"idShort":"TargetPosition","value":"Pallet_B"}},
                    {"value":{"idShort":"requestId","value":"request-1"}},
                    {"value":{"idShort":"runId","value":"run-1"}}
                  ]
                }
                """;

        ResponseEntity<Map<String, Object>> response = controller.moveBox(input, "Robot_02");

        assertEquals(200, response.getStatusCode().value());
        assertEquals("Station_01", response.getBody().get("stationId"));
        assertEquals("Conveyor_A", response.getBody().get("SourcePosition"));
        assertEquals("Pallet_B", response.getBody().get("TargetPosition"));

        ArgumentCaptor<String> payloadCaptor = ArgumentCaptor.forClass(String.class);
        verify(publisher).publishRobotOperation(
                org.mockito.ArgumentMatchers.eq("Robot_02"),
                org.mockito.ArgumentMatchers.eq("moveBox"),
                payloadCaptor.capture());

        JsonObject payload = JsonParser.parseString(payloadCaptor.getValue()).getAsJsonObject();
        assertEquals("Station_01", payload.get("stationId").getAsString());
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
    void moveBoxTranslatesCanonicalIdentitiesUsingParameterSemantics() throws Exception {
        MqttCommandPublisherService publisher = mock(MqttCommandPublisherService.class);
        SimulationIdentityTranslator translator = new SimulationIdentityTranslator();
        translator.setAliases(Map.of(
                "urn:test:conveyor", "Conveyor_01",
                "urn:test:pallet", "Pallet_01"));
        translator.setSourceStations(Map.of("urn:test:conveyor", "Station_01"));
        SimulationMachineOperationController controller =
                new SimulationMachineOperationController(publisher, translator);
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
        assertEquals("Station_01", response.getBody().get("stationId"));
        assertEquals("Conveyor_01", response.getBody().get("SourcePosition"));
        assertEquals("Pallet_01", response.getBody().get("TargetPosition"));

        ArgumentCaptor<String> payloadCaptor = ArgumentCaptor.forClass(String.class);
        verify(publisher).publishRobotOperation(
                org.mockito.ArgumentMatchers.eq("Robot_02"),
                org.mockito.ArgumentMatchers.eq("moveBox"),
                payloadCaptor.capture());
        JsonObject payload = JsonParser.parseString(payloadCaptor.getValue()).getAsJsonObject();
        assertEquals(
                "Conveyor_01",
                payload.getAsJsonObject("params").get("SourcePosition").getAsString());
        assertEquals(
                "Pallet_01",
                payload.getAsJsonObject("params").get("TargetPosition").getAsString());
    }
}
