Feature: hjh (SYN-8)
  Source: Jira ticket SYN-8

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-8
  Scenario: AC1 - acceptance criterion SYN-8-1
    Given the test suite is "functional"
    When I validate the acceptance criterion "acceptance criterion SYN-8-1"
    Then the scenario status should be PASS
