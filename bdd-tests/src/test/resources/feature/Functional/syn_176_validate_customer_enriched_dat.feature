Feature: Validate customer enriched data completeness and accuracy (SYN-176)
  Source: Jira ticket SYN-176

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-176 @SYN-177
  Scenario: AC1 - Given a raw customer record exists in syndicate_raw.customer, When the dbt model
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-176 @SYN-178
  Scenario: AC2 - Given a customer record has a null email field, When customer_enriched is built,
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS
