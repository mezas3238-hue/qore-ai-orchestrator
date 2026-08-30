#!/usr/bin/env python3
"""Verify QORE_CODEX_ENGINEER_TOKEN capabilities without creating GitHub resources."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

REPO = "mezas3238-hue/qore-core"
API = f"https://api.github.com/repos/{REPO}"
USER_AGENT = "qore-ai-orchestrator/1.0"


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _request(token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None
        return exc.code, body


def _require_success(token: str, path: str) -> Any:
    status, body = _request(token, "GET", path)
    if status != 200:
        raise RuntimeError(f"GET {path} returned HTTP {status}")
    return body


def _require_write_probe_422(token: str, path: str, payload: dict[str, Any], label: str) -> None:
    status, _body = _request(token, "POST", path, payload)
    if status != 422:
        raise RuntimeError(f"{label} permission probe returned HTTP {status}; expected 422")


def main() -> int:
    token = os.environ.get("QORE_CODEX_ENGINEER_TOKEN", "").strip()
    if not token:
        print("QORE_CODEX_ENGINEER_TOKEN is not configured.", file=sys.stderr)
        return 2

    repo = _require_success(token, "")
    branch = _require_success(token, "/branches/main")
    _require_success(token, "/actions/runs?per_page=1")
    _require_success(token, "/pulls?state=open&per_page=1")

    if not isinstance(repo, dict) or repo.get("full_name") != REPO:
        print("Repository identity mismatch.", file=sys.stderr)
        return 3
    commit = branch.get("commit") if isinstance(branch, dict) else None
    main_sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(main_sha, str) or len(main_sha) != 40:
        print("Could not bind qore-core main SHA.", file=sys.stderr)
        return 3

    # Valid ref syntax + impossible object SHA. With Contents write permission GitHub
    # reaches semantic validation and returns 422. No ref can be created.
    _require_write_probe_422(
        token,
        "/git/refs",
        {"ref": "refs/heads/__qore_codex_preflight_never_created__", "sha": "0" * 40},
        "Contents write",
    )

    # Missing head guarantees no pull request can be created. With PR write
    # permission GitHub reaches validation and returns 422.
    _require_write_probe_422(
        token,
        "/pulls",
        {
            "title": "QORE CODEX PREFLIGHT — MUST NOT CREATE",
            "head": "__qore_codex_preflight_missing_head__",
            "base": "main",
            "body": "permission probe only",
        },
        "Pull requests write",
    )

    print(f"QORE_CODEX_ENGINEER_PREFLIGHT_OK repo={REPO} main={main_sha}")
    print("Verified: repository read, Actions read, Contents write, Pull requests write; no resource created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
