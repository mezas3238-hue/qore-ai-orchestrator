#!/usr/bin/env python3
"""Augment reviewer state with bounded private PR/issue/CI control-plane evidence.

This collector never exposes provider API credentials. It uses only the GitHub
bridge token and keeps the result bounded so Sol can distinguish a genuinely
pending reviewer job from actionable reviewer-infrastructure work.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CLAUDE_REPO = "mezas3238-hue/qore-claude-reviewer"
DEEPSEEK_REPO = "mezas3238-hue/qore-deepseek-reviewer"
USER_AGENT = "qore-ai-orchestrator/1.0"
MAX_PRS = 8
MAX_ISSUES = 12
MAX_RUNS = 12
MAX_BODY_CHARS = 3500


class ControlPlaneError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api_json(
    repo: str,
    path: str,
    token: str,
    params: dict[str, str] | None = None,
) -> Any:
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}{query}",
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise ControlPlaneError(
                f"{repo}{path}: HTTP {exc.code}; reviewer bridge requires "
                "Pull requests=Read-only, Issues=Read-only, Actions=Read-only"
            ) from exc
        raise ControlPlaneError(f"{repo}{path}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ControlPlaneError(f"{repo}{path}: {type(exc).__name__}") from exc


def _labels(value: Any) -> list[str]:
    result: list[str] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            result.append(item["name"])
        elif isinstance(item, str):
            result.append(item)
    return result


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run.get("id"),
        "name": run.get("name"),
        "event": run.get("event"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "head_branch": run.get("head_branch"),
        "head_sha": run.get("head_sha"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
    }


def _latest_run_for_head(runs: list[dict[str, Any]], head_sha: Any) -> dict[str, Any] | None:
    if not isinstance(head_sha, str):
        return None
    for run in runs:
        if run.get("head_sha") == head_sha:
            return _run_summary(run)
    return None


def collect_repo_control_plane(repo: str, token: str) -> dict[str, Any]:
    pulls_payload = _api_json(
        repo,
        "/pulls",
        token,
        {"state": "open", "sort": "updated", "direction": "desc", "per_page": str(MAX_PRS)},
    )
    issues_payload = _api_json(
        repo,
        "/issues",
        token,
        {"state": "open", "sort": "updated", "direction": "desc", "per_page": str(MAX_ISSUES)},
    )
    runs_payload = _api_json(
        repo,
        "/actions/runs",
        token,
        {"per_page": str(MAX_RUNS)},
    )
    if not isinstance(pulls_payload, list):
        raise ControlPlaneError(f"{repo}: pull list is invalid")
    if not isinstance(issues_payload, list):
        raise ControlPlaneError(f"{repo}: issue list is invalid")
    if not isinstance(runs_payload, dict) or not isinstance(runs_payload.get("workflow_runs"), list):
        raise ControlPlaneError(f"{repo}: workflow run list is invalid")

    runs = [item for item in runs_payload["workflow_runs"] if isinstance(item, dict)]
    pulls: list[dict[str, Any]] = []
    for item in pulls_payload[:MAX_PRS]:
        if not isinstance(item, dict):
            continue
        head = item.get("head") if isinstance(item.get("head"), dict) else {}
        base = item.get("base") if isinstance(item.get("base"), dict) else {}
        head_sha = head.get("sha")
        pulls.append(
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "draft": item.get("draft"),
                "updated_at": item.get("updated_at"),
                "base_ref": base.get("ref"),
                "base_sha": base.get("sha"),
                "head_ref": head.get("ref"),
                "head_sha": head_sha,
                "body": str(item.get("body") or "")[:MAX_BODY_CHARS],
                "latest_head_run": _latest_run_for_head(runs, head_sha),
            }
        )

    issues: list[dict[str, Any]] = []
    for item in issues_payload[:MAX_ISSUES]:
        if not isinstance(item, dict) or isinstance(item.get("pull_request"), dict):
            continue
        issues.append(
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "updated_at": item.get("updated_at"),
                "labels": _labels(item.get("labels")),
                "body": str(item.get("body") or "")[:MAX_BODY_CHARS],
            }
        )

    return {
        "repository": repo,
        "visibility": "AVAILABLE",
        "open_pull_requests": pulls,
        "open_issues": issues,
        "recent_action_runs": [_run_summary(run) for run in runs[:MAX_RUNS]],
    }


def augment_state(state: dict[str, Any], token: str) -> dict[str, Any]:
    if not token:
        state.setdefault("errors", []).append(
            "reviewer-control-plane: QORE_REVIEWER_DISPATCH_TOKEN is missing"
        )
        return state

    for key, repo in (("claude", CLAUDE_REPO), ("deepseek", DEEPSEEK_REPO)):
        reviewer = state.get(key)
        if not isinstance(reviewer, dict):
            state.setdefault("errors", []).append(
                f"reviewer-control-plane:{key}: base reviewer state is unavailable"
            )
            continue
        try:
            reviewer["control_plane"] = collect_repo_control_plane(repo, token)
        except ControlPlaneError as exc:
            reviewer["control_plane"] = {
                "repository": repo,
                "visibility": "UNAVAILABLE",
                "open_pull_requests": [],
                "open_issues": [],
                "recent_action_runs": [],
            }
            state.setdefault("errors", []).append(f"reviewer-control-plane:{key}:{exc}")
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source = Path(args.state)
    state = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise SystemExit("external reviewer state must be an object")

    token = os.environ.get("QORE_REVIEWER_DISPATCH_TOKEN", "").strip()
    augmented = augment_state(state, token)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(augmented, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = augmented.get("errors") if isinstance(augmented.get("errors"), list) else []
    print(
        "REVIEWER_CONTROL_PLANE errors={} claude={} deepseek={}".format(
            len(errors),
            ((augmented.get("claude") or {}).get("control_plane") or {}).get("visibility"),
            ((augmented.get("deepseek") or {}).get("control_plane") or {}).get("visibility"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
