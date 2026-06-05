"""Thin CLI client used by Harness CI steps to invoke the QE agent.

It calls the in-cluster agent Service (deterministic `/qe` endpoints), prints the
JSON result, and sets the process exit code from the deterministic pass/fail so a
Harness Run step gates correctly.

Examples:
    qe-cli suite functional
    qe-cli scenario functional F1
    qe-cli scenarios            # list everything
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = os.environ.get(
    "QE_AGENT_URL",
    "http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080",
)


def _get(url: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def _suite_passed(payload: dict) -> bool:
    return bool(payload.get("passed"))


def _scenario_passed(payload: dict) -> bool:
    return payload.get("status") == "PASS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qe-cli", description="Invoke the QE Quality Agent.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Agent Service base URL.")
    parser.add_argument("--timeout", type=int, default=900, help="Request timeout (seconds).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_suite = sub.add_parser("suite", help="Run an entire test suite.")
    p_suite.add_argument("suite", choices=["static", "standards", "integration", "functional", "nonfunctional"])

    p_scn = sub.add_parser("scenario", help="Run a single scenario.")
    p_scn.add_argument("suite")
    p_scn.add_argument("scenario_id")

    sub.add_parser("scenarios", help="List all suites and scenario ids.")

    args = parser.parse_args(argv)

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
            data = _get(f"{args.base_url}/qe/scenario/{args.suite}/{args.scenario_id}", args.timeout)
            print(json.dumps(data, indent=2))
            return 0 if _scenario_passed(data) else 1
    except urllib.error.URLError as exc:
        print(json.dumps({"status": "ERROR", "error": f"agent unreachable: {exc}"}), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
