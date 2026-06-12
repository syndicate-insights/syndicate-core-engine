Feature: CLONE - dbt transform pipeline latency and SLA compliance for all three domains (SYN-30)
  Source: Jira ticket SYN-30

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-30
  Scenario: AC1 - When dbt-account-transform, dbt-address-transform, and dbt-customer-transform Cr
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-30
  Scenario: AC2 - Given a dbt run fails, When the CronJob retries, Then the retry must not produce
    Given the test suite is "non_functional"
    When I run scenario "N2"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-30
  Scenario: AC3 - When any dbt model fails with a test error, Then a failure entry must appear in
    Given the test suite is "non_functional"
    When I run scenario "N2"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-30
  Scenario: AC4 - Given the BigQuery dataset contains more than 1 million raw rows, When the stagi
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS
