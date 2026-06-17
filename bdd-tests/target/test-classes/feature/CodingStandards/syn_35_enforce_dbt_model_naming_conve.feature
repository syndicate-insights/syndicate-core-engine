Feature: Enforce dbt model naming conventions and manifest lint standards (SYN-35)
  Source: Jira ticket SYN-35

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-35
  Scenario: AC1 - When the static analysis suite runs against dbt-account-transform, dbt-address-t
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-35
  Scenario: AC2 - When schema.yml files are linted, Then every model listed under models must have
    Given the test suite is "static_analysis"
    When I run scenario "SA1"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-35
  Scenario: AC3 - Given a new dbt model is added without a corresponding entry in schema.yml, When
    Given the test suite is "non_functional"
    When I run scenario "N2"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-35
  Scenario: AC4 - When sources.yml files are validated, Then every source table must declare a loa
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS
