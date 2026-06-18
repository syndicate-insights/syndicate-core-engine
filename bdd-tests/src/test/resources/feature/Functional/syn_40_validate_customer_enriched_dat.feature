Feature: Validate customer enriched data completeness and accuracy (SYN-40)
  Source: Jira ticket SYN-40

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-40
  Scenario: AC1 - Given a raw customer record exists in syndicate_raw.customer, When the dbt model
    Given the test suite is "integration"
    When I run scenario "I2"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-40
  Scenario: AC2 - Given a customer record has a null email field, When customer_enriched is built,
    Given the test suite is "non_functional"
    When I run scenario "N2"
    Then the scenario status should be PASS
