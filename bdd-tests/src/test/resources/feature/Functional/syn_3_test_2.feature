Feature: test 2 (SYN-3)
  Source: Jira ticket SYN-3

  Background:
    Given the QE Quality Agent is reachable

  @JiraGenerated @SYN-3
  Scenario: AC1 - acceptance criterion SYN-3-1
    Given the test suite is "functional"
    When I validate the acceptance criterion "acceptance criterion SYN-3-1"
    Then the scenario status should be PASS
