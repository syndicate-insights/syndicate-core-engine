Feature: CLONE - dbt transform pipeline latency and SLA compliance for all three domains (SYN-25)
  Source: Jira ticket SYN-25

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-25
  Scenario: AC1 - When dbt-account-transform, dbt-address-transform, and dbt-customer-transform Cr
    When dbt-account-transform, dbt-address-transform, and dbt-customer-transform CronJobs run, Then each pipeline must complete within 10 minutes of scheduled start time

  @JiraGenerated @SYN-25
  Scenario: AC2 - Given a dbt run fails, When the CronJob retries, Then the retry must not produce
    Given a dbt run fails, When the CronJob retries, Then the retry must not produce duplicate rows in the mart tables

  @JiraGenerated @SYN-25
  Scenario: AC3 - When any dbt model fails with a test error, Then a failure entry must appear in
    When any dbt model fails with a test error, Then a failure entry must appear in processed_files_metadata within 2 minutes of the error occurring

  @JiraGenerated @SYN-25
  Scenario: AC4 - Given the BigQuery dataset contains more than 1 million raw rows, When the stagi
    Given the BigQuery dataset contains more than 1 million raw rows, When the staging model runs, Then query execution time should not exceed 5 minutes
