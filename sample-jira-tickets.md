# Sample JIRA Tickets

---

**Project:** SYN
**Issue Type:** Story
**Summary:** Validate customer enriched data completeness and accuracy

**Description:**
As a data consumer, I want the customer_enriched mart table to contain complete, deduplicated, and accurately transformed customer records so that downstream reporting is reliable.

**Acceptance Criteria:**
- Given a raw customer record exists in syndicate_raw.customer, When the dbt model customer_enriched runs, Then the output row count should equal the deduplicated source count
- Given a customer record has a null email field, When customer_enriched is built, Then the row should be excluded from the mart and logged in processed_files_metadata
- When customer_enriched is queried, Then every row must have a non-null customer_id, full_name, and created_at value
- Given the same customer_id appears more than once in the source, When the staging model stg_customer_raw runs, Then only the latest record by ingestion timestamp should be retained

---

**Project:** SYN
**Issue Type:** Story
**Summary:** BigQuery to Neo4j ingest pipeline produces correct graph relationships

**Description:**
As a graph consumer, I want the neo4j-ingest job to correctly translate enriched BigQuery mart tables into Neo4j nodes and relationships so that the knowledge graph reflects the latest syndicate data.

**Acceptance Criteria:**
- Given customer_enriched, account_enriched, and address_enriched BigQuery tables are populated, When the neo4j-ingest CronJob runs, Then nodes of type Customer, Account, and Address should be created in Neo4j
- Given a Customer node exists in Neo4j, When the ingest pipeline runs, Then a HAS_ACCOUNT relationship must link each Customer to their corresponding Account nodes
- When the ingest CronJob completes, Then the processed_files_metadata table in BigQuery must contain a row recording the run timestamp and row counts for each mart
- Given a duplicate customer_id is present in the source BigQuery table, When neo4j-ingest runs, Then the MERGE clause in graph-model.cypher should prevent duplicate nodes from being created

---

**Project:** SYN
**Issue Type:** Story
**Summary:** dbt transform pipeline latency and SLA compliance for all three domains

**Description:**
As a platform engineer, I want the account, address, and customer dbt transforms to complete within defined SLA windows so that enriched data is available for downstream consumers on schedule.

**Acceptance Criteria:**
- When dbt-account-transform, dbt-address-transform, and dbt-customer-transform CronJobs run, Then each pipeline must complete within 10 minutes of scheduled start time
- Given a dbt run fails, When the CronJob retries, Then the retry must not produce duplicate rows in the mart tables
- When any dbt model fails with a test error, Then a failure entry must appear in processed_files_metadata within 2 minutes of the error occurring
- Given the BigQuery dataset contains more than 1 million raw rows, When the staging model runs, Then query execution time should not exceed 5 minutes

---

**Project:** SYN
**Issue Type:** Story
**Summary:** Enforce dbt model naming conventions and manifest lint standards

**Description:**
As a platform engineer, I want all dbt models, sources, and schema files to follow agreed naming conventions so that the codebase is maintainable and CI gates catch violations early.

**Acceptance Criteria:**
- When the static analysis suite runs against dbt-account-transform, dbt-address-transform, and dbt-customer-transform, Then all staging models must be prefixed with stg_ and all mart models must not have a prefix
- When schema.yml files are linted, Then every model listed under models must have at least one column with a description and a not_null test
- Given a new dbt model is added without a corresponding entry in schema.yml, When the coding standards scenario runs, Then the check must fail with a missing schema entry error
- When sources.yml files are validated, Then every source table must declare a loaded_at_field for freshness checking
