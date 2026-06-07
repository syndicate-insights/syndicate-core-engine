Feature: Non-functional posture of the syndicate-core-engine pipeline
  As an SRE
  I want SLAs, reliability and security controls to hold continuously
  So that the pipeline runs predictably and safely on GKE

  Background:
    Given the QE Quality Agent is reachable
    And the test suite is "nonfunctional"

  @NonFunctional @PerformanceSla
  Scenario: N1 - jobs finish inside their SLA windows and BigQuery latency is OK
    When I run scenario "N1"
    Then the scenario status should be PASS

  @NonFunctional @ReliabilitySecurity
  Scenario: N2 - cronjob policies, secret sourcing and pod logs are healthy
    When I run scenario "N2"
    Then the scenario status should be PASS

  @NonFunctional @Suite
  Scenario: Run the whole non-functional suite
    When I run the whole "nonfunctional" suite
    Then the suite should pass
