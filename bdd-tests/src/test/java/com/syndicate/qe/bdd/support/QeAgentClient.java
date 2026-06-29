package com.syndicate.qe.bdd.support;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * Thin client over the QE Quality Agent's deterministic /qe/... endpoints.
 *
 * The Cucumber step definitions delegate to this client so the deterministic
 * scenarios remain the single source of truth — same code path that Harness
 * already gates on, just exposed in Gherkin.
 */
public final class QeAgentClient {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final HttpClient http;
    private final String baseUrl;
    private final Duration timeout;

    public QeAgentClient() {
        this(envOrDefault("QE_AGENT_URL",
                "http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080"),
                Duration.ofSeconds(Long.parseLong(envOrDefault("QE_AGENT_TIMEOUT_SECONDS", "900"))));
    }

    public QeAgentClient(String baseUrl, Duration timeout) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.timeout = timeout;
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(15)).build();
    }

    public JsonNode runScenario(String suite, String scenarioId) {
        return get("/qe/scenario/" + suite + "/" + scenarioId);
    }

    /** Execute an agent-generated read-only BigQuery check and assert a column. */
    public JsonNode runCheck(String sql, String column, long equals) {
        ObjectNode body = MAPPER.createObjectNode();
        body.put("sql", sql);
        body.put("column", column);
        body.put("equals", equals);
        return post("/qe/query/check", body.toString());
    }

    public JsonNode runSuite(String suite) {
        return get("/qe/suite/" + suite);
    }

    public JsonNode listScenarios() {
        return get("/qe/scenarios");
    }

    private JsonNode get(String path) {
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + path))
                    .timeout(timeout)
                    .header("Accept", "application/json")
                    .GET()
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() / 100 != 2) {
                throw new IllegalStateException(
                        "QE agent " + path + " returned HTTP " + resp.statusCode() + ": " + resp.body());
            }
            return MAPPER.readTree(resp.body());
        } catch (Exception e) {
            throw new IllegalStateException("QE agent call failed for " + path + ": " + e.getMessage(), e);
        }
    }

    private JsonNode post(String path, String jsonBody) {
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + path))
                    .timeout(timeout)
                    .header("Accept", "application/json")
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(jsonBody))
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() / 100 != 2) {
                throw new IllegalStateException(
                        "QE agent " + path + " returned HTTP " + resp.statusCode() + ": " + resp.body());
            }
            return MAPPER.readTree(resp.body());
        } catch (Exception e) {
            throw new IllegalStateException("QE agent call failed for " + path + ": " + e.getMessage(), e);
        }
    }

    private static String envOrDefault(String key, String fallback) {
        String v = System.getenv(key);
        if (v == null || v.isBlank()) {
            v = System.getProperty(key);
        }
        return (v == null || v.isBlank()) ? fallback : v;
    }
}
