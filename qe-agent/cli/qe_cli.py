"""Thin CLI client used by Harness CI steps to invoke the QE agent.

It calls the in-cluster agent Service (deterministic `/qe` endpoints), prints
the JSON result, and sets the process exit code from the deterministic
pass/fail so a Harness Run step gates correctly.

Examples:
    qe-cli suite functional
    qe-cli scenario functional F1
    qe-cli scenarios                                 # list everything
    qe-cli jira ac SYN-123
    qe-cli jira author SYN-123 [--dry-run]
    qe-cli jira sync-results --ticket SYN-123 \\
        --cucumber-json bdd-tests/target/cucumber.json \\
        --pipeline bdd_tests --execution <plan_execution_id>
    qe-cli jira reconcile SYN-123 --execution <plan_execution_id>
    qe-cli harness latest
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = os.environ.get(
    "QE_AGENT_URL",
    "http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080",
)


def _get(url: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def _post(url: str, timeout: int, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(  # noqa: S310
        url, data=data, method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode() or "{}")


def _suite_passed(payload: dict) -> bool:
    return bool(payload.get("passed"))


def _scenario_passed(payload: dict) -> bool:
    return payload.get("status") == "PASS"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qe-cli", description="Invoke the QE Quality Agent.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Agent Service base URL.")
    parser.add_argument("--timeout", type=int, default=900, help="Request timeout (seconds).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_suite = sub.add_parser("suite", help="Run an entire test suite.")
    p_suite.add_argument(
        "suite",
        choices=["static", "standards", "integration", "functional", "nonfunctional"],
    )

    p_scn = sub.add_parser("scenario", help="Run a single scenario.")
    p_scn.add_argument("suite")
    p_scn.add_argument("scenario_id")

    sub.add_parser("scenarios", help="List all suites and scenario ids.")

    p_jira = sub.add_parser("jira", help="Jira / BDD authoring helpers.")
    jira_sub = p_jira.add_subparsers(dest="jira_command", required=True)

    j_ac = jira_sub.add_parser("ac", help="Print the acceptance-criteria bullets for a ticket.")
    j_ac.add_argument("ticket")

    j_author = jira_sub.add_parser("author", help="Author BDD scenarios from a Jira ticket.")
    j_author.add_argument("ticket")
    j_author.add_argument("--dry-run", action="store_true")

    j_sync = jira_sub.add_parser("sync-results", help="Push Cucumber results to Jira.")
    j_sync.add_argument("--ticket", required=True)
    j_sync.add_argument("--cucumber-json", required=True)
    j_sync.add_argument("--execution", default=None,
                        help="Harness execution id (used to build a UI link).")
    j_sync.add_argument("--pipeline", default=None, help="Harness pipeline id.")

    j_rec = jira_sub.add_parser(
        "reconcile",
        help="Update BDD scenarios after a failing Harness run.",
    )
    j_rec.add_argument("ticket")
    j_rec.add_argument("--execution", default=None)

    p_harness = sub.add_parser("harness", help="Harness pipeline helpers.")
    h_sub = p_harness.add_subparsers(dest="harness_command", required=True)
    h_sub.add_parser("latest", help="Latest BDD pipeline execution status.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "scenarios":
            data = _get(f"{args.base_url}/qe/scenarios", args.timeout)
            print(json.dumps(data, indent=2))
            return 0
        if args.command == "suite":
            data = _get(f"{args.base_url}/qe/suite/{args.suite}", args.timeout)
            print(json.dumps(data, indent=2))
            return 0 if _suite_passed(data) else 1
        if args.command == "scenario":
            data = _get(
                f"{args.base_url}/qe/scenario/{args.suite}/{args.scenario_id}",
                args.timeout,
            )
            print(json.dumps(data, indent=2))
            return 0 if _scenario_passed(data) else 1
        if args.command == "jira":
            return _handle_jira(args)
        if args.command == "harness":
            return _handle_harness(args)
    except urllib.error.URLError as exc:
        print(json.dumps({"status": "ERROR", "error": f"agent unreachable: {exc}"}),
              file=sys.stderr)
        return 2
    return 2


def _handle_jira(args: argparse.Namespace) -> int:
    base = args.base_url
    if args.jira_command == "ac":
        data = _get(
            f"{base}/qe/jira/{urllib.parse.quote(args.ticket)}/acceptance-criteria",
            args.timeout,
        )
        print(json.dumps(data, indent=2))
        return 0 if data.get("bullets") else 1
    if args.jira_command == "author":
        url = f"{base}/qe/jira/{urllib.parse.quote(args.ticket)}/author"
        if args.dry_run:
            url += "?dry_run=true"
        data = _post(url, args.timeout)
        print(json.dumps(data, indent=2))
        return 0 if data.get("pr") or args.dry_run else 1
    if args.jira_command == "sync-results":
        params = {"cucumber_json_path": args.cucumber_json}
        if args.execution and args.pipeline:
            harness_base = os.environ.get("HARNESS_BASE_URL", "https://app.harness.io")
            account = os.environ.get("HARNESS_ACCOUNT_ID", "")
            org = os.environ.get("HARNESS_ORG_ID", "default")
            project = os.environ.get("HARNESS_PROJECT_ID", "")
            params["execution_url"] = (
                f"{harness_base}/ng/account/{account}/cd/orgs/{org}/projects/"
                f"{project}/pipelines/{args.pipeline}/executions/"
                f"{args.execution}/pipeline"
            )
        url = (
            f"{base}/qe/jira/{urllib.parse.quote(args.ticket)}/sync-results?"
            + urllib.parse.urlencode(params)
        )
        data = _post(url, args.timeout)
        print(json.dumps(data, indent=2))
        return 0 if data.get("failed", 0) == 0 else 1
    if args.jira_command == "reconcile":
        url = f"{base}/qe/jira/{urllib.parse.quote(args.ticket)}/reconcile"
        if args.execution:
            url += f"?plan_execution_id={urllib.parse.quote(args.execution)}"
        data = _post(url, args.timeout)
        print(json.dumps(data, indent=2))
        return 0 if data.get("pr") else 1
    return 2


def _handle_harness(args: argparse.Namespace) -> int:
    if args.harness_command == "latest":
        data = _get(f"{args.base_url}/qe/harness/bdd/latest", args.timeout)
        print(json.dumps(data, indent=2))
        return 0 if data.get("status") == "SUCCESS" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
