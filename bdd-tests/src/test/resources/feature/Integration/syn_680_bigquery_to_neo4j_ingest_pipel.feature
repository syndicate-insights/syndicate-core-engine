Feature: BigQuery to Neo4j ingest pipeline produces correct graph relationships (SYN-680)
  Source: Jira ticket SYN-680

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-680 @SYN-681
  Scenario: AC1 - Given customer_enriched, account_enriched, and address_enriched BigQuery tables
    When I capture the BigQuery value:
      """
      SELECT (SELECT COUNT(*) FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.customer_enriched`) + (SELECT COUNT(*) FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.account_enriched`) + (SELECT COUNT(*) FROM `project-61358164-b71e-4422-a5c.qe_hack_syndicate_insight.address_enriched`) AS value
      """
    And I capture the Neo4j value:
      """
      MATCH (n) WHERE n:Customer OR n:Account OR n:Address RETURN count(n) AS value
      """
    Then the BigQuery and Neo4j values should be equal

  @JiraGenerated @SYN-680 @SYN-682
  Scenario: AC2 - Given a Customer node exists in Neo4j, When the ingest pipeline runs, Then a HAS
    When I run the Neo4j check:
      """
      MATCH (c:Customer) WHERE NOT (c)-[:HAS_ACCOUNT]->() RETURN count(c) AS violations
      """
    Then the result column "violations" should be 0
