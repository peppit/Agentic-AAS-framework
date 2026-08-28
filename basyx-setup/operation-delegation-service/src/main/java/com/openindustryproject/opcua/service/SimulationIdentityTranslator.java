package com.openindustryproject.opcua.service;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;

/**
 * OIP-specific translation below the AAS Operation boundary.
 *
 * The orchestrator and AAS invocation use canonical AAS identities. Only this
 * device adapter is allowed to know simulator node and station names.
 */
@Component
@ConfigurationProperties(prefix = "simulation.identity")
public class SimulationIdentityTranslator {

    private Map<String, String> aliases = new HashMap<>();
    private Map<String, String> sourceStations = new HashMap<>();

    public String toLocalName(String canonicalOrLocalIdentity) {
        if (canonicalOrLocalIdentity == null || canonicalOrLocalIdentity.isBlank()) {
            return canonicalOrLocalIdentity;
        }
        String translated = aliases.get(canonicalOrLocalIdentity);
        if (translated != null && !translated.isBlank()) {
            return translated;
        }
        if (canonicalOrLocalIdentity.startsWith("urn:")) {
            throw new IllegalArgumentException(
                    "No OIP identity alias configured for " + canonicalOrLocalIdentity);
        }
        return canonicalOrLocalIdentity;
    }

    public String stationForSource(String canonicalSource) {
        if (canonicalSource == null || canonicalSource.isBlank()) {
            return null;
        }
        String station = sourceStations.get(canonicalSource);
        return station == null || station.isBlank() ? null : station;
    }

    public Map<String, String> getAliases() {
        return aliases;
    }

    public void setAliases(Map<String, String> aliases) {
        this.aliases = aliases == null ? new HashMap<>() : new HashMap<>(aliases);
    }

    public Map<String, String> getSourceStations() {
        return sourceStations;
    }

    public void setSourceStations(Map<String, String> sourceStations) {
        this.sourceStations = sourceStations == null
                ? new HashMap<>()
                : new HashMap<>(sourceStations);
    }
}
