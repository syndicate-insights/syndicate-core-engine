Feature: Validate customer enriched data completeness and accuracy (SYN-10)
  Source: Jira ticket SYN-10

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-10
  Scenario: AC1 - Given a raw customer record exists in syndicate_raw.customer, When the dbt model
    Given a raw customer record exists in syndicate_raw.customer, When the dbt model customer_enriched runs, Then the output row count should equal the deduplicated source count

  @JiraGenerated @SYN-10
  Scenario: AC2 - Given a customer record has a null email field, When customer_enriched is built,
    Given a customer record has a null email field, When customer_enriched is built, Then the row should be excluded from the mart and logged in processed_files_metadata

  @JiraGenerated @SYN-10
  Scenario: AC3 - When customer_enriched is queried, Then every row must have a non-null customer_
    When customer_enriched is queried, Then every row must have a non-null customer_id, full_name, and created_at value

  @JiraGenerated @SYN-10
  Scenario: AC4 - Given the same customer_id appears more than once in the source, When the stagin
    Given the same customer_id appears more than once in the source, When the staging model stg_customer_raw runs, Then only the latest record by ingestion timestamp should be retained
