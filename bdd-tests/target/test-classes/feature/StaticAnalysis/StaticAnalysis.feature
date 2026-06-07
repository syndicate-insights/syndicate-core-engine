Feature: Static code analysis of the syndicate-core-engine repository
  As a quality engineer
  I want every commit to pass deterministic linters and security scans
  So that defects, style violations and hardcoded secrets are caught before runtime

  Background:
    Given the QE Quality Agent is reachable
    And the test suite is "static"

  @Static @SqlLint
  Scenario: SA1 - dbt SQL lint passes (sqlfluff)
    When I run scenario "SA1"
    Then the scenario status should be PASS
    And there should be no findings

  @Static @PythonLint
  Scenario: SA2 - Python lint passes (ruff)
    When I run scenario "SA2"
    Then the scenario status should be PASS

  @Static @SecurityScan
  Scenario: SA3 - Python security scan passes (bandit)
    When I run scenario "SA3"
    Then the scenario status should be PASS

  @Static @YamlLint
  Scenario: SA4 - YAML lint passes (yamllint)
    When I run scenario "SA4"
    Then the scenario status should be PASS

  @Static @SecretScan
  Scenario: SA5 - No hardcoded secrets in YAML or SQL
    When I run scenario "SA5"
    Then the scenario status should be PASS
    And the metric "hits" should be 0

  @Static @Suite
  Scenario: Run the whole static-analysis suite
    When I run the whole "static" suite
    Then the suite should pass
