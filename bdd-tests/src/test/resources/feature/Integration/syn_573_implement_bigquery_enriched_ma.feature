Feature: Implement BigQuery enriched marts are refreshed within 24 hours (SYN-573)
  Source: Jira ticket SYN-573

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-573 @SYN-574
  Scenario: AC1 - When customer_enriched is queried, Then the most recent processed_at timestamp m
    When I run the BigQuery check:
      """
      SELECT COUNTIF(max_processed_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)) AS violations FROM (SELECT MAX(processed_at) AS max_processed_at FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.customer_enriched`)
      """
    Then the result column "violations" should be 0

  @JiraGenerated @SYN-573 @SYN-575
  Scenario: AC2 - When account_enriched is queried, Then the most recent processed_at timestamp mu
    When I run the BigQuery check:
      """
      SELECT
        COUNTIF(max_processed_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)) AS violations
      FROM (
        SELECT
          MAX(processed_at) AS max_processed_at
        FROM
          `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.account_enriched`
      )
      """
    Then the result column "violations" should be 0
