Feature: BigQuery to Neo4j ingest pipeline produces correct graph relationships (SYN-7)
  Source: Jira ticket SYN-7

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-7
  Scenario: AC1 - Given customer_enriched, account_enriched, and address_enriched BigQuery tables
    Given customer_enriched, account_enriched, and address_enriched BigQuery tables are populated, When the neo4j-ingest CronJob runs, Then nodes of type Customer, Account, and Address should be created in Neo4j

  @JiraGenerated @SYN-7
  Scenario: AC2 - Given a Customer node exists in Neo4j, When the ingest pipeline runs, Then a HAS
    Given a Customer node exists in Neo4j, When the ingest pipeline runs, Then a HAS_ACCOUNT relationship must link each Customer to their corresponding Account nodes

  @JiraGenerated @SYN-7
  Scenario: AC3 - When the ingest CronJob completes, Then the processed_files_metadata table in Bi
    When the ingest CronJob completes, Then the processed_files_metadata table in BigQuery must contain a row recording the run timestamp and row counts for each mart

  @JiraGenerated @SYN-7
  Scenario: AC4 - Given a duplicate customer_id is present in the source BigQuery table, When neo4
    Given a duplicate customer_id is present in the source BigQuery table, When neo4j-ingest runs, Then the MERGE clause in graph-model.cypher should prevent duplicate nodes from being created
