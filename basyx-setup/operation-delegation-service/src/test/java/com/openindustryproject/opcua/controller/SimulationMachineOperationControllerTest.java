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
                    {"value":{"idShort":"requestId","value":"request-1"}}
                  ]
                }
                """;

        ResponseEntity<Map<String, Object>> response = controller.moveBox(input);

        assertEquals(200, response.getStatusCode().value());
        assertEquals("Station_01", response.getBody().get("stationId"));
        assertEquals("Conveyor_A", response.getBody().get("SourcePosition"));
        assertEquals("Pallet_B", response.getBody().get("TargetPosition"));

        ArgumentCaptor<String> payloadCaptor = ArgumentCaptor.forClass(String.class);
        verify(publisher).publishStationOperation(
                org.mockito.ArgumentMatchers.eq("Station_01"),
                org.mockito.ArgumentMatchers.eq("moveBox"),
                payloadCaptor.capture());

        JsonObject payload = JsonParser.parseString(payloadCaptor.getValue()).getAsJsonObject();
        assertEquals("Station_01", payload.get("stationId").getAsString());
        assertEquals(
                "Conveyor_A",
                payload.getAsJsonObject("params").get("SourcePosition").getAsString());
        assertEquals(
                "Pallet_B",
                payload.getAsJsonObject("params").get("TargetPosition").getAsString());
    }
}
