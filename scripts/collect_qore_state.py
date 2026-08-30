#!/usr/bin/env python3
"""Build a bounded, read-only QORE state snapshot for the resident architect."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = "mezas3238-hue/qore-core"
API = f"https://api.github.com/repos/{REPO}"
USER_AGENT = "qore-ai-orchestrator/1.0"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def api_json(path: str, params: dict[str, str] | None, errors: list[str]) -> Any:
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        API + path + query,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        errors.append(f"github_api:{path}:{type(exc).__name__}")
        return None


def read_documents(root: Path, pattern: str) -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            docs.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "content": path.read_text(encoding="utf-8"),
                }
            )
    return docs


def compact_pr(pr: dict[str, Any]) -> dict[str, Any]:
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "base_ref": base.get("ref") if isinstance(base, dict) else None,
        "base_sha": base.get("sha") if isinstance(base, dict) else None,
        "head_ref": head.get("ref") if isinstance(head, dict) else None,
        "head_sha": head.get("sha") if isinstance(head, dict) else None,
        "synthetic_sha": pr.get("merge_commit_sha"),
        "body": (pr.get("body") or "")[:4000],
    }


def compact_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "labels": [
            label.get("name")
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        ],
        "body": (issue.get("body") or "")[:4000],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.repo_dir).resolve()
    output = Path(args.output).resolve()
    errors: list[str] = []

    checkout_main_sha = git(root, "rev-parse", "HEAD")
    tree_sha = git(root, "show", "-s", "--format=%T", "HEAD")
    recent_raw = git(root, "log", "-20", "--pretty=format:%H%x09%s")
    recent_commits = [
        {
            "sha": line.split("\t", 1)[0],
            "subject": line.split("\t", 1)[1] if "\t" in line else "",
        }
        for line in recent_raw.splitlines()
        if line.strip()
    ]

    branch_raw = api_json("/branches/main", None, errors)
    pulls_raw = api_json("/pulls", {"state": "open", "per_page": "100"}, errors)
    issues_raw = api_json("/issues", {"state": "open", "per_page": "100"}, errors)
    runs_raw = api_json("/actions/runs", {"branch": "main", "per_page": "20"}, errors)

    live_main_sha: str | None = None
    protected: bool | None = None
    required_status_contexts: list[str] = []
    if isinstance(branch_raw, dict):
        commit = branch_raw.get("commit")
        if isinstance(commit, dict) and isinstance(commit.get("sha"), str):
            live_main_sha = commit["sha"]
        if isinstance(branch_raw.get("protected"), bool):
            protected = branch_raw["protected"]
        protection = branch_raw.get("protection")
        if isinstance(protection, dict):
            checks = protection.get("required_status_checks")
            if isinstance(checks, dict) and isinstance(checks.get("contexts"), list):
                required_status_contexts = [
                    value for value in checks["contexts"] if isinstance(value, str)
                ]

    snapshot_consistent = live_main_sha == checkout_main_sha if live_main_sha else False
    if not snapshot_consistent:
        errors.append("main_sha_changed_or_unverifiable_during_snapshot")

    pulls = [compact_pr(x) for x in pulls_raw] if isinstance(pulls_raw, list) else []
    issues = (
        [
            compact_issue(x)
            for x in issues_raw
            if isinstance(x, dict) and "pull_request" not in x
        ]
        if isinstance(issues_raw, list)
        else []
    )

    runs: list[dict[str, Any]] = []
    if isinstance(runs_raw, dict):
        for run in runs_raw.get("workflow_runs", []):
            if not isinstance(run, dict):
                continue
            runs.append(
                {
                    "id": run.get("id"),
                    "name": run.get("name"),
                    "event": run.get("event"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "head_sha": run.get("head_sha"),
                    "created_at": run.get("created_at"),
                    "updated_at": run.get("updated_at"),
                }
            )

    snapshot = {
        "schema_version": "qore.state.snapshot.v1",
        "repository": REPO,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "main_sha": checkout_main_sha,
        "live_main_sha": live_main_sha,
        "tree_sha": tree_sha,
        "snapshot_consistent": snapshot_consistent,
        "branch_protection": {
            "protected": protected,
            "required_status_contexts": required_status_contexts,
        },
        "recent_commits": recent_commits,
        "open_pull_requests": pulls,
        "open_issues": issues,
        "recent_main_action_runs": runs,
        "readme": (
            (root / "README.md").read_text(encoding="utf-8")
            if (root / "README.md").is_file()
            else ""
        ),
        "constitution_documents": read_documents(root, "docs/constitution/*.md"),
        "roadmap_documents": read_documents(root, "docs/roadmap/*.md"),
        "mission_document_paths": [
            p.relative_to(root).as_posix()
            for p in sorted(root.glob("docs/missions/**/*.md"))
            if p.is_file()
        ],
        "architecture_document_paths": [
            p.relative_to(root).as_posix()
            for p in sorted(root.glob("docs/architecture/**/*.md"))
            if p.is_file()
        ],
        "collection_errors": errors,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"QORE state snapshot: main={checkout_main_sha} tree={tree_sha}")
    print(
        "roadmaps={} open_prs={} open_issues={} errors={} consistent={}".format(
            len(snapshot["roadmap_documents"]),
            len(pulls),
            len(issues),
            len(errors),
            snapshot_consistent,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
