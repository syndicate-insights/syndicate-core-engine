Feature: Validate customer phone number normalisation (SYN-206)
  Source: Jira ticket SYN-206

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-206 @SYN-207 @manual
  Scenario: AC1 - Given a raw phone value containing spaces, dashes or parentheses, When customer_
    Then this scenario requires manual verification

  @JiraGenerated @SYN-206 @SYN-208 @manual
  Scenario: AC2 - Given the original phone column, When customer_enriched is built, Then phone_num
    Then this scenario requires manual verification
