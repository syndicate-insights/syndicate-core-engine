Feature: Functional business rules of the dbt marts and Neo4j graph
  As a product owner
  I want the documented transformation rules to hold for every batch
  So that downstream consumers can rely on the enriched data

  Background:
    Given the QE Quality Agent is reachable
    And the test suite is "functional"

  @Functional @InvestmentRulePositive
  Scenario: F1 - rows matching the predicate are reclassified as INVESTMENT
    When I run scenario "F1"
    Then the scenario status should be PASS

  @Functional @InvestmentRuleNegative
  Scenario: F2 - rows not matching the predicate keep their original account_type
    When I run scenario "F2"
    Then the scenario status should be PASS

  @Functional @AddressComposition
  Scenario: F3 - full_address equals "line1, city, postcode, country"
    When I run scenario "F3"
    Then the scenario status should be PASS

  @Functional @PhoneNormalisation
  Scenario: F4 - phone_number is the digits-only form of phone
    When I run scenario "F4"
    Then the scenario status should be PASS

  @Functional @DbtSchemaTests
  Scenario: F5 - declared dbt schema/data tests pass (not_null, unique on PKs)
    When I run scenario "F5"
    Then the scenario status should be PASS

  @Functional @Neo4jConstraints
  Scenario: F6 - Neo4j constraints exist and relationship cardinality is sane
    When I run scenario "F6"
    Then the scenario status should be PASS

  @Functional @Suite
  Scenario: Run the whole functional suite
    When I run the whole "functional" suite
    Then the suite should pass
