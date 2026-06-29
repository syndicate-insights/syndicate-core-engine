Feature: Validate customer phone number normalisation (SYN-236)
  Source: Jira ticket SYN-236

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-236 @SYN-237
  Scenario: AC1 - Given a raw phone value containing spaces, dashes or parentheses, When customer_
    When I run the BigQuery check:
      """
      SELECT COUNTIF(REGEXP_CONTAINS(phone_number, r'[^0-9]')) AS violations FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.customer_enriched`
      """
    Then the result column "violations" should be 0

  @JiraGenerated @SYN-236 @SYN-238
  Scenario: AC2 - Given the original phone column, When customer_enriched is built, Then phone_num
    When I run the BigQuery check:
      """
      SELECT COUNTIF(t.phone_number != REGEXP_REPLACE(t.phone, '[^0-9]', '')) AS violations FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.customer_enriched` AS t
      """
    Then the result column "violations" should be 0
