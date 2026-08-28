Feature: Implement data integrity across BigQuery marts, dbt transformations, and the   Neo4j graph (SYN-831)
  Source: Jira ticket SYN-831

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-831 @SYN-832
  Scenario: AC1 - (BigQuery) When customer_enriched is queried, Then there must be zero rows where
    When I run the BigQuery check:
      """
      SELECT COUNTIF(customer_id IS NULL OR phone_number IS NULL) AS violations FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.customer_enriched`
      """
    Then the result column "violations" should be 0

  @JiraGenerated @SYN-831 @SYN-833 @manual
  Scenario: AC2 - (dbt) When address_enriched is built by dbt, Then full_address must equal line1,
    Then this scenario requires manual verification

  @JiraGenerated @SYN-831 @SYN-834
  Scenario: AC3 - (Neo4j) When the Neo4j graph is queried, Then the number of Customer nodes must
    When I capture the BigQuery value:
      """
      SELECT COUNT(DISTINCT customer_id) AS value FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.customer_enriched`
      """
    And I capture the Neo4j value:
      """
      MATCH (c:Customer) RETURN COUNT(c) AS value
      """
    Then the BigQuery and Neo4j values should be equal
