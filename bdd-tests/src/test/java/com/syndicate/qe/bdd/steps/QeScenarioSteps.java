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
    private String lastSql;

    @Given("the QE Quality Agent is reachable")
    public void the_qe_agent_is_reachable() {
        JsonNode scenarios = agent.listScenarios();
        assertThat(scenarios).isNotNull();
        assertThat(scenarios.size()).isGreaterThan(0);
    }

    // --- Agent-generated, embedded BigQuery checks ---------------------------
    // The authoring agent writes the actual verification SQL into the feature
    // file; these generic steps execute it read-only via the agent and gate on
    // the asserted result column.

    @When("I run the BigQuery check:")
    public void i_run_the_bigquery_check(String sql) {
        this.lastSql = sql;
    }

    @Then("the result column {string} should be {long}")
    public void the_result_column_should_be(String column, long expected) {
        assertThat(lastSql).as("a BigQuery check must be run first").isNotBlank();
        JsonNode res = agent.runCheck(lastSql, column, expected);
        assertThat(res.path("status").asText("ERROR"))
                .as("BigQuery check column=%s actual=%s expected=%s findings=%s",
                        column, res.path("actual"), res.path("expected"), res.path("findings"))
                .isEqualTo("PASS");
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
