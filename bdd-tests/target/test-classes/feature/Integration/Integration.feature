Feature: Integration of GCS, BigQuery and Neo4j stages
  As a data engineer
  I want each stage of the syndicate pipeline to be consistent with the next
  So that customers, accounts and addresses arrive intact in the graph

  Background:
    Given the QE Quality Agent is reachable
    And the test suite is "integration"

  @Integration @GcsToBq
  Scenario: I1 - GCS CSV rows land in the BigQuery raw tables
    When I run scenario "I1"
    Then the scenario status should be PASS

  @Integration @RawToEnriched
  Scenario: I2 - enriched tables are populated and FK-consistent with raw
    When I run scenario "I2"
    Then the scenario status should be PASS

  @Integration @BqToNeo4j
  Scenario: I3 - enriched rows are ingested into Neo4j nodes and relationships
    When I run scenario "I3"
    Then the scenario status should be PASS

  @Integration @Watermark
  Scenario: I4 - ingest watermark advances and processed_files_metadata dedupes
    When I run scenario "I4"
    Then the scenario status should be PASS

  @Integration @EndToEnd
  Scenario: I5 - sampled customer accounts are consistent across BQ and Neo4j
    When I run scenario "I5"
    Then the scenario status should be PASS

  @Integration @Suite
  Scenario: Run the whole integration suite
    When I run the whole "integration" suite
    Then the suite should pass
