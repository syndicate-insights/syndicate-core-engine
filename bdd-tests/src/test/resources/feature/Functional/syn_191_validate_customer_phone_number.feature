Feature: Validate customer phone number normalisation (SYN-191)
  Source: Jira ticket SYN-191

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-191 @SYN-192
  Scenario: AC1 - Given a raw phone value containing spaces, dashes or parentheses, When customer_
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-191 @SYN-193
  Scenario: AC2 - Given the original phone column, When customer_enriched is built, Then phone_num
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS
