Feature: dbt transform pipeline latency and SLA compliance for all three domains (SYN-75)
  Source: Jira ticket SYN-75

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-75 @SYN-90
  Scenario: AC1 - When dbt-account-transform, dbt-address-transform, and dbt-customer-transform Cr
    Given the test suite is "functional"
    When I run scenario "F5"
    Then the scenario status should be PASS

  @JiraGenerated @SYN-75 @SYN-91
  Scenario: AC2 - Given a dbt run fails, When the CronJob retries, Then the retry must not produce
    Given the test suite is "non_functional"
    When I run scenario "N2"
    Then the scenario status should be PASS
