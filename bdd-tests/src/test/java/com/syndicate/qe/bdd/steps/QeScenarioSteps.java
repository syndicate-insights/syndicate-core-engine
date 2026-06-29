package com.syndicate.qe.bdd.steps;

import com.fasterxml.jackson.databind.JsonNode;
import com.syndicate.qe.bdd.support.QeAgentClient;
import io.cucumber.java.PendingException;
import io.cucumber.java.en.Given;
import io.cucumber.java.en.Then;
import io.cucumber.java.en.When;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Generic step definitions that exercise any QE scenario or suite by id.
 *
 * Every feature file in this BDD pack reuses these steps; the differences live
 * in the Gherkin (scenario id + suite name) so authoring new tests is cheap.
 */
public class QeScenarioSteps {

    private final QeAgentClient agent = new QeAgentClient();

    private JsonNode lastScenario;
    private JsonNode lastSuite;
    private String currentSuite;
    private String lastQuery;
    private String lastQueryKind;   // "bigquery" or "neo4j"
    private Long capturedBqValue;
    private Long capturedNeoValue;

    @Given("the QE Quality Agent is reachable")
    public void the_qe_agent_is_reachable() {
        JsonNode scenarios = agent.listScenarios();
        assertThat(scenarios).isNotNull();
        assertThat(scenarios.size()).isGreaterThan(0);
    }

    // --- Agent-generated, embedded checks -----------------------------------
    // The authoring agent writes the actual verification query into the feature
    // file (BigQuery SQL or Neo4j Cypher); these generic steps execute it
    // read-only via the agent and gate on the asserted result column.

    @When("I run the BigQuery check:")
    public void i_run_the_bigquery_check(String sql) {
        this.lastQuery = sql;
        this.lastQueryKind = "bigquery";
    }

    @When("I run the Neo4j check:")
    public void i_run_the_neo4j_check(String cypher) {
        this.lastQuery = cypher;
        this.lastQueryKind = "neo4j";
    }

    @Then("the result column {string} should be {long}")
    public void the_result_column_should_be(String column, long expected) {
        assertThat(lastQuery).as("a check must be run first").isNotBlank();
        JsonNode res = "neo4j".equals(lastQueryKind)
                ? agent.runCypherCheck(lastQuery, column, expected)
                : agent.runCheck(lastQuery, column, expected);
        assertThat(res.path("status").asText("ERROR"))
                .as("%s check column=%s actual=%s expected=%s findings=%s",
                        lastQueryKind, column, res.path("actual"),
                        res.path("expected"), res.path("findings"))
                .isEqualTo("PASS");
    }

    // --- Cross-system checks (capture a value from each system, compare) -----

    @When("I capture the BigQuery value:")
    public void i_capture_the_bigquery_value(String sql) {
        JsonNode res = agent.queryValue(sql);
        assertThat(res.path("status").asText("ERROR"))
                .as("BigQuery value error: %s", res.path("findings")).isEqualTo("OK");
        this.capturedBqValue = res.path("value").asLong();
    }

    @When("I capture the Neo4j value:")
    public void i_capture_the_neo4j_value(String cypher) {
        JsonNode res = agent.cypherValue(cypher);
        assertThat(res.path("status").asText("ERROR"))
                .as("Neo4j value error: %s", res.path("findings")).isEqualTo("OK");
        this.capturedNeoValue = res.path("value").asLong();
    }

    @Then("the BigQuery and Neo4j values should be equal")
    public void the_bigquery_and_neo4j_values_should_be_equal() {
        assertThat(capturedBqValue).as("a BigQuery value must be captured first").isNotNull();
        assertThat(capturedNeoValue).as("a Neo4j value must be captured first").isNotNull();
        assertThat(capturedBqValue)
                .as("BigQuery value (%s) should equal Neo4j value (%s)", capturedBqValue, capturedNeoValue)
                .isEqualTo(capturedNeoValue);
    }

    @Then("this scenario requires manual verification")
    public void this_scenario_requires_manual_verification() {
        // No automated check could be generated for this acceptance criterion.
        // Report pending (skipped) so it never silently passes nor fails the gate.
        throw new PendingException("Acceptance criterion requires manual verification");
    }

    @Given("the test suite is {string}")
    public void the_test_suite_is(String suite) {
        this.currentSuite = suite;
    }

    @When("I run scenario {string}")
    public void i_run_scenario(String scenarioId) {
        assertThat(currentSuite).as("current suite must be set first").isNotBlank();
        lastScenario = agent.runScenario(currentSuite, scenarioId);
    }

    @When("I run the whole {string} suite")
    public void i_run_the_whole_suite(String suite) {
        lastSuite = agent.runSuite(suite);
    }

    @Then("the scenario status should be {word}")
    public void the_scenario_status_should_be(String expected) {
        assertThat(lastScenario).isNotNull();
        String actual = lastScenario.path("status").asText("MISSING");
        assertThat(actual)
                .as("scenario %s findings=%s",
                        lastScenario.path("scenario_id").asText(),
                        lastScenario.path("findings"))
                .isEqualTo(expected);
    }

    @Then("the suite should pass")
    public void the_suite_should_pass() {
        assertThat(lastSuite).isNotNull();
        assertThat(lastSuite.path("passed").asBoolean(false))
                .as("suite results: %s", lastSuite)
                .isTrue();
    }

    @Then("the metric {string} should be {long}")
    public void the_metric_should_be(String key, long expected) {
        assertThat(lastScenario).isNotNull();
        long actual = lastScenario.path("metrics").path(key).asLong(Long.MIN_VALUE);
        assertThat(actual).as("metric %s on scenario %s", key,
                lastScenario.path("scenario_id").asText()).isEqualTo(expected);
    }

    @Then("there should be no findings")
    public void there_should_be_no_findings() {
        assertThat(lastScenario).isNotNull();
        JsonNode findings = lastScenario.path("findings");
        assertThat(findings.isArray() ? findings.size() : 0)
                .as("findings: %s", findings).isZero();
    }
}
