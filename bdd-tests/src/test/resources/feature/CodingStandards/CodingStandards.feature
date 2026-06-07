Feature: Repository coding standards governance
  As a platform engineer
  I want dbt, Kubernetes and FK conventions to be enforced automatically
  So that the pipeline stays consistent and reviewable

  Background:
    Given the QE Quality Agent is reachable
    And the test suite is "standards"

  @Standards @DbtNaming
  Scenario: CS1 - dbt models follow stg_/_enriched naming convention
    When I run scenario "CS1"
    Then the scenario status should be PASS

  @Standards @PrimaryKeyTests
  Scenario: CS2 - every enriched model declares a not_null PK test
    When I run scenario "CS2"
    Then the scenario status should be PASS

  @Standards @SourcesDocumented
  Scenario: CS3 - dbt sources are documented
    When I run scenario "CS3"
    Then the scenario status should be PASS

  @Standards @K8sHygiene
  Scenario: CS4 - Kubernetes manifests pin images, set limits and use a service account
    When I run scenario "CS4"
    Then the scenario status should be PASS

  @Standards @ForeignKeyNaming
  Scenario: CS5 - foreign-key column is consistently named customer_id
    When I run scenario "CS5"
    Then the scenario status should be PASS
    And the metric "violations" should be 0

  @Standards @Suite
  Scenario: Run the whole coding-standards suite
    When I run the whole "standards" suite
    Then the suite should pass
