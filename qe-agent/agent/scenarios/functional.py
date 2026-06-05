"""Functional testing suite (goal 4) — 6 scenarios.

Validates the business transformation rules implemented by the dbt marts and the
Neo4j graph model:
  - account_enriched INVESTMENT reclassification rule
  - address_enriched full_address composition
  - customer_enriched phone normalisation
  - declared dbt data tests (not_null / unique on PKs)
  - Neo4j constraints + relationship cardinality
"""

from __future__ import annotations

from agent.config import SETTINGS
from agent.results import ScenarioResult, Status
from agent.tools import bigquery_toolset as bq
from agent.tools import dbt_toolset as dbt
from agent.tools import neo4j_toolset as neo

SUITE = "functional"

# Rule: account_type -> INVESTMENT when sort_code last digit in (2,3,5,7)
# AND the last two account_number digits are BOTH even.
_INVESTMENT_PREDICATE = (
    "CAST(SUBSTR(REGEXP_REPLACE(sort_code, r'[^0-9]', ''), -1, 1) AS INT64) IN (2,3,5,7) "
    "AND MOD(CAST(SUBSTR(account_number, -2, 1) AS INT64), 2) = 0 "
    "AND MOD(CAST(SUBSTR(account_number, -1, 1) AS INT64), 2) = 0"
)


def f1_investment_rule_positive() -> ScenarioResult:
    """F1: rows matching the predicate are classified INVESTMENT."""
    r = ScenarioResult("F1", SUITE, "INVESTMENT reclassification (positive)")
    tbl = SETTINGS.fq_table("account_enriched")
    res = bq.run_query(
        f"SELECT COUNTIF(account_type != 'INVESTMENT') AS misclassified, COUNT(*) AS matched "
        f"FROM `{tbl}` WHERE {_INVESTMENT_PREDICATE}"
    )
    row = res.get("rows", [{}])[0]
    r.actual = row
    r.expected = {"misclassified": 0}
    if row.get("matched", 0) and row.get("misclassified", 0) > 0:
        r.fail(f"{row['misclassified']} rows match INVESTMENT predicate but are not labelled INVESTMENT.")
    return r


def f2_investment_rule_negative() -> ScenarioResult:
    """F2: rows NOT matching the predicate retain their original type."""
    r = ScenarioResult("F2", SUITE, "INVESTMENT reclassification (negative / no over-reach)")
    enr = SETTINGS.fq_table("account_enriched")
    # No row should be INVESTMENT unless it satisfies the predicate.
    res = bq.run_query(
        f"SELECT COUNT(*) AS wrongly_investment FROM `{enr}` "
        f"WHERE account_type = 'INVESTMENT' AND NOT ({_INVESTMENT_PREDICATE})"
    )
    n = res.get("rows", [{}])[0].get("wrongly_investment", 0)
    r.actual = {"wrongly_investment": n}
    r.expected = {"wrongly_investment": 0}
    if n and n > 0:
        r.fail(f"{n} rows are INVESTMENT without satisfying the rule predicate.")
    return r


def f3_full_address_format() -> ScenarioResult:
    """F3: full_address == 'line1, city, postcode, country'."""
    r = ScenarioResult("F3", SUITE, "Address composition (full_address)")
    enr = SETTINGS.fq_table("address_enriched")
    res = bq.run_query(
        f"SELECT COUNT(*) AS mismatched FROM `{enr}` "
        f"WHERE full_address != CONCAT(line1, ', ', city, ', ', postcode, ', ', country)"
    )
    n = res.get("rows", [{}])[0].get("mismatched", 0)
    r.actual = {"mismatched": n}
    r.expected = {"mismatched": 0}
    if n and n > 0:
        r.fail(f"{n} rows have full_address inconsistent with components.")
    return r


def f4_phone_normalisation() -> ScenarioResult:
    """F4: phone_number is the digits-only form of phone."""
    r = ScenarioResult("F4", SUITE, "Phone normalisation (digits only)")
    enr = SETTINGS.fq_table("customer_enriched")
    res = bq.run_query(
        f"SELECT COUNTIF(phone_number != REGEXP_REPLACE(phone, r'[^0-9]', '')) AS mismatched, "
        f"COUNTIF(REGEXP_CONTAINS(phone_number, r'[^0-9]')) AS non_digit "
        f"FROM `{enr}`"
    )
    row = res.get("rows", [{}])[0]
    r.actual = row
    r.expected = {"mismatched": 0, "non_digit": 0}
    if row.get("mismatched", 0) or row.get("non_digit", 0):
        r.fail("phone_number not consistently normalised to digits-only.")
    return r


def f5_dbt_data_tests() -> ScenarioResult:
    """F5: declared dbt schema tests (not_null/unique on PKs) pass."""
    r = ScenarioResult("F5", SUITE, "dbt schema/data tests (not_null & unique)")
    summary = {}
    failed = False
    for entity in SETTINGS.dbt_projects:
        res = dbt.dbt_test(entity)
        ts = res.get("test_summary", {})
        summary[entity] = {k: ts.get(k) for k in ("pass", "fail", "error")} if ts else res
        if res.get("returncode", 1) != 0 or ts.get("fail") or ts.get("error"):
            failed = True
    r.actual = summary
    if failed:
        r.fail("One or more dbt data tests failed.")
    return r


def f6_neo4j_constraints() -> ScenarioResult:
    """F6: uniqueness constraints exist and relationship cardinality is sane."""
    r = ScenarioResult("F6", SUITE, "Neo4j constraints + relationship cardinality")
    constraints = neo.list_constraints()
    rows = constraints.get("rows", [])
    have = {tuple(c.get("labelsOrTypes", [])) + tuple(c.get("properties", [])) for c in rows}
    required = [("Customer", "customer_id"), ("Account", "account_id"), ("Address", "address_id")]
    missing = [f"{lbl}.{prop}" for lbl, prop in required if (lbl, prop) not in have]
    # Every Account/Address should connect to exactly one Customer.
    dangling_acc = neo.run_cypher(
        "MATCH (a:Account) WHERE NOT (:Customer)-[:HAS_ACCOUNT]->(a) RETURN count(a) AS n"
    ).get("rows", [{}])[0].get("n", 0)
    dangling_addr = neo.run_cypher(
        "MATCH (d:Address) WHERE NOT (:Customer)-[:HAS_ADDRESS]->(d) RETURN count(d) AS n"
    ).get("rows", [{}])[0].get("n", 0)
    r.actual = {"missing_constraints": missing, "dangling_accounts": dangling_acc, "dangling_addresses": dangling_addr}
    if missing or dangling_acc or dangling_addr:
        r.fail("Missing uniqueness constraints or dangling nodes without an owning Customer.")
    return r


REGISTRY = {
    "F1": f1_investment_rule_positive,
    "F2": f2_investment_rule_negative,
    "F3": f3_full_address_format,
    "F4": f4_phone_normalisation,
    "F5": f5_dbt_data_tests,
    "F6": f6_neo4j_constraints,
}


def run_all() -> list[dict]:
    out = []
    for fn in REGISTRY.values():
        try:
            out.append(fn().to_dict())
        except Exception as exc:  # noqa: BLE001
            out.append(ScenarioResult("?", SUITE, fn.__name__, Status.ERROR, findings=[str(exc)]).to_dict())
    return out
