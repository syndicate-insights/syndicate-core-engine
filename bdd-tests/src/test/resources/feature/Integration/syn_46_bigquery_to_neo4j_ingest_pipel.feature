Feature: BigQuery to Neo4j ingest pipeline produces correct graph relationships (SYN-46)
  Source: Jira ticket SYN-46

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-46 @SYN-61
  Scenario: AC1 - Given customer_enriched, account_enriched, and address_enriched BigQuery tables
    Given the test suite is "integration"
    When I run scenario "I3"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-46 @SYN-62
  Scenario: AC2 - Given a Customer node exists in Neo4j, When the ingest pipeline runs, Then a HAS
    Given the test suite is "integration"
    When I run scenario "I3"
    Then the scenario status should be PASS
