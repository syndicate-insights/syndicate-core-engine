Feature: Validate customer phone number normalisation (SYN-221)
  Source: Jira ticket SYN-221

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-221 @SYN-222 @manual
  Scenario: AC1 - Given a raw phone value containing spaces, dashes or parentheses, When customer_
    Then this scenario requires manual verification

  @JiraGenerated @SYN-221 @SYN-223 @manual
  Scenario: AC2 - Given the original phone column, When customer_enriched is built, Then phone_num
    Then this scenario requires manual verification
