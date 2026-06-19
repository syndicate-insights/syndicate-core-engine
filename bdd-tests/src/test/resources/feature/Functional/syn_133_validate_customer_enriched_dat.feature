Feature: Validate customer enriched data completeness and accuracy (SYN-133)
  Source: Jira ticket SYN-133

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-133 @SYN-162
  Scenario: AC1 - Given a raw customer record exists in syndicate_raw.customer, When the dbt model
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-133 @SYN-163
  Scenario: AC2 - Given a customer record has a null email field, When customer_enriched is built,
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS
