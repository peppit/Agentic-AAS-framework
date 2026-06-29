package com.konecranes.opcua.service;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;

@Service
public class MqttCommandPublisherService {

    private static final Logger logger = LoggerFactory.getLogger(MqttCommandPublisherService.class);

    @Value("${simulation.mqtt.enabled:true}")
    private boolean enabled;

    @Value("${simulation.mqtt.broker-url:tcp://mosquitto:1883}")
    private String brokerUrl;

    @Value("${simulation.mqtt.client-id:simulation-operation-service}")
    private String clientId;

    @Value("${simulation.mqtt.qos:1}")
    private int qos;

    @Value("${simulation.mqtt.topic-template:simulation/{stationId}/command/{operation}}")
    private String topicTemplate;

    @Value("${simulation.mqtt.conveyor.running-topic:oip/command/conveyorbelt/running}")
    private String conveyorRunningTopic;

    @Value("${simulation.mqtt.conveyor.speed-topic:oip/command/conveyorbelt/speed}")
    private String conveyorSpeedTopic;

    private MqttClient client;

    @PostConstruct
    public void init() {
        if (!enabled) {
            logger.info("Simulation MQTT publisher is disabled");
            return;
        }

        try {
            MqttConnectOptions options = new MqttConnectOptions();
            options.setAutomaticReconnect(true);
            options.setCleanSession(true);
            options.setConnectionTimeout(10);

            client = new MqttClient(brokerUrl, clientId, new MemoryPersistence());
            client.connect(options);

            logger.info("Connected simulation MQTT publisher to {}", brokerUrl);
        } catch (Exception e) {
            logger.warn("Could not connect simulation MQTT publisher at startup. Will retry on first publish: {}", e.getMessage());
        }
    }

    @PreDestroy
    public void destroy() {
        if (client == null) {
            return;
        }

        try {
            if (client.isConnected()) {
                client.disconnect();
            }
            client.close();
        } catch (MqttException e) {
            logger.warn("Error closing simulation MQTT publisher", e);
        }
    }

    public void publishConveyorRunning(String payload) throws Exception {
        publish(conveyorRunningTopic, payload);
    }

    public void publishConveyorSpeed(String payload) throws Exception {
        publish(conveyorSpeedTopic, payload);
    }

    public void publishStationOperation(String stationId, String operation, String payload) throws Exception {
        String topic = topicTemplate
                .replace("{stationId}", sanitizeTopicPart(stationId))
                .replace("{operation}", sanitizeTopicPart(operation));
        publish(topic, payload);
    }

    private synchronized void publish(String topic, String payload) throws Exception {
        if (!enabled) {
            throw new IllegalStateException("Simulation MQTT publisher is disabled");
        }

        ensureConnected();
        MqttMessage message = new MqttMessage(payload.getBytes(StandardCharsets.UTF_8));
        message.setQos(qos);
        client.publish(topic, message);
        logger.info("Published simulation command to topic {} payload={}", topic, payload);
    }

    private void ensureConnected() throws Exception {
        if (client == null) {
            client = new MqttClient(brokerUrl, clientId, new MemoryPersistence());
        }

        if (client.isConnected()) {
            return;
        }

        MqttConnectOptions options = new MqttConnectOptions();
        options.setAutomaticReconnect(true);
        options.setCleanSession(true);
        options.setConnectionTimeout(10);
        client.connect(options);
    }

    private String sanitizeTopicPart(String value) {
        if (value == null || value.isBlank()) {
            return "unknown";
        }

        // Replace MQTT topic separator and wildcard symbols to keep topic shape stable.
        return value.trim().replace('/', '_').replace('+', '_').replace('#', '_');
    }
}
