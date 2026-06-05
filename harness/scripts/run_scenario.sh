#!/usr/bin/env bash
# Invoke one QE scenario against the in-cluster QE agent and gate on the result.
#
# Usage: run_scenario.sh <suite> <scenario_id>
#   suite      : static | standards | integration | functional | nonfunctional
#   scenario_id: e.g. SA1, CS2, I3, F5, N1
#
# Exit code 0 => PASS, 1 => FAIL/ERROR (fails the Harness step).
# Requires curl. jq is used when available, with a grep fallback otherwise.
set -euo pipefail

SUITE="${1:?suite required}"
SCENARIO="${2:?scenario id required}"
BASE_URL="${QE_AGENT_URL:-http://qe-quality-agent.qe-hack-syndicate.svc.cluster.local:8080}"
URL="${BASE_URL}/qe/scenario/${SUITE}/${SCENARIO}"

echo ">> QE ${SUITE}/${SCENARIO} -> ${URL}"
RESP="$(curl -fsS --max-time 900 "${URL}")"
echo "${RESP}"

if command -v jq >/dev/null 2>&1; then
  STATUS="$(printf '%s' "${RESP}" | jq -r '.status')"
else
  STATUS="$(printf '%s' "${RESP}" | grep -o '"status"[[:space:]]*:[[:space:]]*"[A-Z]*"' | head -1 | grep -o '[A-Z]*$')"
fi

echo ">> status=${STATUS}"
if [ "${STATUS}" = "PASS" ]; then
  exit 0
fi
echo "!! QE scenario ${SUITE}/${SCENARIO} did not pass (status=${STATUS})" >&2
exit 1
