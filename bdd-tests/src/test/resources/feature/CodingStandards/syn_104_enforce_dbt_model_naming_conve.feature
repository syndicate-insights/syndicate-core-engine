Feature: Enforce dbt model naming conventions and manifest lint standards (SYN-104)
  Source: Jira ticket SYN-104

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-104 @SYN-119
  Scenario: AC1 - When the static analysis suite runs against dbt-account-transform, dbt-address-t
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-104 @SYN-120
  Scenario: AC2 - When schema.yml files are linted, Then every model listed under models must have
    Given the test suite is "static_analysis"
    When I run scenario "SA1"
    Then the scenario status should be PASS
