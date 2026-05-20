// Neo4j Graph Model - Syndicate Core Engine
// Nodes: Customer, Account, Address
// Relationships: HAS_ACCOUNT, HAS_ADDRESS

// ─── CONSTRAINTS ────────────────────────────────────────────────────────────

CREATE CONSTRAINT customer_id IF NOT EXISTS
FOR (c:Customer) REQUIRE c.customer_id IS UNIQUE;

CREATE CONSTRAINT account_id IF NOT EXISTS
FOR (a:Account) REQUIRE a.account_id IS UNIQUE;

CREATE CONSTRAINT address_id IF NOT EXISTS
FOR (d:Address) REQUIRE d.address_id IS UNIQUE;

// ─── NODE: Customer ─────────────────────────────────────────────────────────
// Source: customer_enriched (dbt curated)
//
// Properties:
//   customer_id   : STRING (PK)
//   first_name    : STRING
//   last_name     : STRING
//   dob_raw       : STRING
//   email         : STRING
//   phone         : STRING  (original raw)
//   phone_number  : STRING  (cleaned, digits only)

// ─── NODE: Account ──────────────────────────────────────────────────────────
// Source: account_enriched (dbt curated)
//
// Properties:
//   account_id      : STRING (PK)
//   account_type    : STRING (SAVINGS | CURRENT | INVESTMENT)
//   sort_code       : STRING (XX-XX-XX format)
//   account_number  : STRING
//   opened_date_raw : STRING

// ─── NODE: Address ──────────────────────────────────────────────────────────
// Source: address_enriched (dbt curated)
//
// Properties:
//   address_id   : STRING (PK)
//   line1        : STRING
//   city         : STRING
//   postcode     : STRING
//   country      : STRING
//   full_address : STRING (composite: line1, city, postcode, country)

// ─── RELATIONSHIPS ──────────────────────────────────────────────────────────
//
// (Customer)-[:HAS_ACCOUNT]->(Account)
//   Linked via: customer_id
//   A customer can have one or more accounts.
//
// (Customer)-[:HAS_ADDRESS]->(Address)
//   Linked via: customer_id
//   A customer can have one or more addresses.

// ─── VISUAL MODEL ───────────────────────────────────────────────────────────
//
//  ┌─────────────────────────────┐
//  │          Customer           │
//  ├─────────────────────────────┤
//  │ customer_id    (PK, UNIQUE) │
//  │ first_name                  │
//  │ last_name                   │
//  │ dob_raw                     │
//  │ email                       │
//  │ phone                       │
//  │ phone_number                │
//  └──────────┬──────────┬───────┘
//             │          │
//   HAS_ACCOUNT        HAS_ADDRESS
//             │          │
//             ▼          ▼
//  ┌──────────────────┐  ┌──────────────────┐
//  │     Account      │  │     Address      │
//  ├──────────────────┤  ├──────────────────┤
//  │ account_id  (PK) │  │ address_id  (PK) │
//  │ account_type     │  │ line1            │
//  │ sort_code        │  │ city             │
//  │ account_number   │  │ postcode         │
//  │ opened_date_raw  │  │ country          │
//  └──────────────────┘  │ full_address     │
//                        └──────────────────┘
