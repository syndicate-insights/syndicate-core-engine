Feature: Implement data integrity across BigQuery marts, dbt transformations, and the   Neo4j graph (SYN-512)
  Source: Jira ticket SYN-512

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-512 @SYN-513
  Scenario: AC1 - (BigQuery) When customer_enriched is queried, Then there must be zero rows where
    When I run the BigQuery check:
      """
      SELECT COUNTIF(customer_id IS NULL) AS violations FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.customer_enriched`
      """
    Then the result column "violations" should be 0

  @JiraGenerated @SYN-512 @SYN-514
  Scenario: AC2 - (dbt) When address_enriched is built by dbt, Then full_address must equal line1,
    When I run the BigQuery check:
      """
      SELECT
        COUNTIF(full_address != CONCAT(line1, ', ', city, ',')) AS violations
      FROM
        `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.address_enriched`
      """
    Then the result column "violations" should be 0

  @JiraGenerated @SYN-512 @SYN-515
  Scenario: AC3 - (Neo4j) When the Neo4j graph is queried, Then the number of Customer nodes must
    When I run the Neo4j check:
      """
      MATCH (c:Customer) RETURN count(c) AS violations
      """
    Then the result column "violations" should be 0
