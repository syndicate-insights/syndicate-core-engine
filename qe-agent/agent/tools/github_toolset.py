"""GitHub REST API toolset (no external deps).

Used by the `bdd_authoring_agent` to:
  * write / update `.feature` files under `bdd-tests/`,
  * push them on a feature branch,
  * raise a PR against the syndicate-core-engine repo when a Cucumber scenario
    starts failing because the underlying acceptance criteria changed.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Iterable
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


GITHUB_API = _env("GITHUB_API_URL", "https://api.github.com")
GITHUB_TOKEN = _env("GITHUB_TOKEN")
GITHUB_REPO = _env("GITHUB_REPO", "syndicate-insights/syndicate-core-engine")
GIT_DEFAULT_BRANCH = _env("GIT_DEFAULT_BRANCH", "main")
GIT_AUTHOR_NAME = _env("GIT_AUTHOR_NAME", "qe-quality-agent")
GIT_AUTHOR_EMAIL = _env("GIT_AUTHOR_EMAIL", "qe-agent@syndicate.local")


def _request(method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN must be set.")
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    logger.debug("github %s %s body_keys=%s", method, url, list(body.keys()) if body else None)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            payload = resp.read().decode()
            result = json.loads(payload) if payload else {}
            logger.debug("github %s %s -> 2xx response_keys=%s", method, url, list(result.keys()) if isinstance(result, dict) else type(result).__name__)
            return result
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="ignore")
        logger.error("github %s %s -> HTTP %s: %s", method, url, exc.code, body_text)
        return {"error": exc.code, "detail": body_text}


def _branch_ref(branch: str) -> dict:
    return _request("GET", f"/repos/{GITHUB_REPO}/git/ref/heads/{quote(branch, safe='')}")


def create_branch(branch: str, base: str | None = None) -> dict:
    """Create `branch` from `base` (default: GIT_DEFAULT_BRANCH)."""
    base = base or GIT_DEFAULT_BRANCH
    base_ref = _branch_ref(base)
    if "error" in base_ref:
        return base_ref
    sha = base_ref["object"]["sha"]
    return _request("POST", f"/repos/{GITHUB_REPO}/git/refs", {
        "ref": f"refs/heads/{branch}",
        "sha": sha,
    })


def put_file(branch: str, path: str, content: str, message: str) -> dict:
    """Create or update a file on `branch`."""
    existing = _request("GET", f"/repos/{GITHUB_REPO}/contents/{quote(path)}?ref={quote(branch)}")
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
        "branch": branch,
        "committer": {"name": GIT_AUTHOR_NAME, "email": GIT_AUTHOR_EMAIL},
    }
    if isinstance(existing, dict) and existing.get("sha"):
        body["sha"] = existing["sha"]
    return _request("PUT", f"/repos/{GITHUB_REPO}/contents/{quote(path)}", body)


def open_pr(branch: str, title: str, body: str, base: str | None = None,
            labels: Iterable[str] | None = None) -> dict:
    pr = _request("POST", f"/repos/{GITHUB_REPO}/pulls", {
        "title": title,
        "head": branch,
        "base": base or GIT_DEFAULT_BRANCH,
        "body": body,
        "maintainer_can_modify": True,
    })
    if "error" not in pr and labels:
        _request("POST", f"/repos/{GITHUB_REPO}/issues/{pr['number']}/labels",
                 {"labels": list(labels)})
    return pr


def author_feature_pr(ticket: str, feature_path: str, feature_content: str,
                      summary: str, description: str,
                      labels: Iterable[str] | None = None) -> dict:
    """One-shot helper: branch + write feature file + open PR."""
    branch = f"qe-agent/{ticket.lower()}-{int(time.time())}"
    logger.info("author_feature_pr: ticket=%s branch=%s feature_path=%s", ticket, branch, feature_path)
    cb = create_branch(branch)
    if "error" in cb and cb.get("error") != 422:  # 422 = already exists
        logger.error("author_feature_pr: branch creation failed ticket=%s branch=%s error=%s", ticket, branch, cb)
        return cb
    logger.debug("author_feature_pr: branch created ticket=%s branch=%s", ticket, branch)
    write = put_file(branch, feature_path, feature_content,
                     message=f"qe-agent: BDD scenarios for {ticket} - {summary}")
    if "error" in write:
        logger.error("author_feature_pr: file write failed ticket=%s path=%s error=%s", ticket, feature_path, write)
        return write
    logger.debug("author_feature_pr: file written ticket=%s path=%s", ticket, feature_path)
    pr = open_pr(branch, f"[{ticket}] {summary}", description,
                 labels=list(labels or ["qe-agent", "bdd", "auto-generated"]))
    if "error" in pr:
        logger.error("author_feature_pr: PR open failed ticket=%s error=%s", ticket, pr)
    else:
        logger.info("author_feature_pr: PR opened ticket=%s pr_url=%s", ticket, pr.get("html_url"))
    return pr
