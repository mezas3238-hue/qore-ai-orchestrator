#!/usr/bin/env python3
"""Build bounded model-facing contexts from the full canonical QORE snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

MODEL_CONTEXT_SCHEMA = "qore.model.context.v1"
MAX_ARCHITECT_CONTEXT_CHARS = 180_000
MAX_ENGINEER_CONTEXT_CHARS = 70_000
MISSION_EXCERPT_CHARS = 700
FOCUSED_ISSUE_BODY_CHARS = 3500
FOCUSED_COMMENT_BODY_CHARS = 5000
FOCUSED_REVIEW_BODY_CHARS = 8000
STALE_CLAUDE_REVIEW_CHARS = 1200
ISSUE_REF_RE = re.compile(r"(?<![\w/])#(?P<number>\d{1,6})\b")


def compact_json_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def issue_refs(text: str) -> list[int]:
    return sorted({int(match.group("number")) for match in ISSUE_REF_RE.finditer(text)})


def pr_index(pr: dict[str, Any]) -> dict[str, Any]:
    refs = issue_refs(str(pr.get("body") or ""))
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "updated_at": pr.get("updated_at"),
        "base_sha": pr.get("base_sha"),
        "head_sha": pr.get("head_sha"),
        "synthetic_sha": pr.get("synthetic_sha"),
        "linked_issue_numbers": refs,
    }


def issue_index(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "updated_at": issue.get("updated_at"),
        "labels": issue.get("labels") if isinstance(issue.get("labels"), list) else [],
    }


def _updated_key(item: dict[str, Any]) -> str:
    value = item.get("updated_at")
    return value if isinstance(value, str) else ""


def focus_pr_numbers(snapshot: dict[str, Any]) -> list[int]:
    pulls = [pr for pr in snapshot.get("open_pull_requests", []) if isinstance(pr, dict)]
    open_numbers = {
        pr.get("number")
        for pr in pulls
        if type(pr.get("number")) is int and pr.get("number") > 0
    }
    focused: list[int] = []

    external = snapshot.get("external_reviewer_state")
    if isinstance(external, dict):
        for reviewer_name in ("deepseek", "claude"):
            reviewer = external.get(reviewer_name)
            if not isinstance(reviewer, dict):
                continue
            current = reviewer.get("current_request")
            if not isinstance(current, dict):
                continue
            number = current.get("pr_number")
            if type(number) is int and number in open_numbers and number not in focused:
                focused.append(number)

    if focused:
        return focused[:2]

    for pr in sorted(pulls, key=_updated_key, reverse=True):
        number = pr.get("number")
        if type(number) is int and number > 0 and number not in focused:
            focused.append(number)
        if len(focused) >= 2:
            break
    return focused[:2]


def compact_focused_pr(pr: dict[str, Any]) -> dict[str, Any]:
    result = pr_index(pr)
    result["body"] = str(pr.get("body") or "")[:4000]
    result["reviews"] = []
    for review in pr.get("reviews", []):
        if not isinstance(review, dict):
            continue
        result["reviews"].append(
            {
                "id": review.get("id"),
                "user": review.get("user"),
                "state": review.get("state"),
                "commit_id": review.get("commit_id"),
                "submitted_at": review.get("submitted_at"),
                "body": str(review.get("body") or "")[:FOCUSED_REVIEW_BODY_CHARS],
            }
        )
    result["conversation_comments"] = []
    for comment in pr.get("conversation_comments", []):
        if not isinstance(comment, dict):
            continue
        result["conversation_comments"].append(
            {
                "id": comment.get("id"),
                "user": comment.get("user"),
                "created_at": comment.get("created_at"),
                "updated_at": comment.get("updated_at"),
                "body": str(comment.get("body") or "")[:FOCUSED_COMMENT_BODY_CHARS],
            }
        )
    return result


def compact_external(snapshot: dict[str, Any], focused_prs: set[int]) -> dict[str, Any]:
    external = snapshot.get("external_reviewer_state")
    if not isinstance(external, dict):
        return {}

    copied = json.loads(json.dumps(external))
    claude = copied.get("claude")
    if isinstance(claude, dict):
        current = claude.get("current_request")
        current_pr = current.get("pr_number") if isinstance(current, dict) else None
        review = claude.get("review")
        if isinstance(review, dict) and current_pr not in focused_prs:
            text = str(review.get("text") or "")
            review["text"] = text[:STALE_CLAUDE_REVIEW_CHARS]
            review["text_truncated_for_model_context"] = len(text) > STALE_CLAUDE_REVIEW_CHARS
    return copied


def mission_index(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in snapshot.get("mission_document_heads", []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        head = item.get("head")
        if isinstance(path, str) and isinstance(head, str):
            result.append({"path": path, "excerpt": head[:MISSION_EXCERPT_CHARS]})
    return result


def build_context(snapshot: dict[str, Any], snapshot_bytes: bytes) -> dict[str, Any]:
    if snapshot.get("schema_version") != "qore.state.snapshot.v1":
        raise ValueError("unexpected source snapshot schema")

    pulls = [pr for pr in snapshot.get("open_pull_requests", []) if isinstance(pr, dict)]
    issues = [issue for issue in snapshot.get("open_issues", []) if isinstance(issue, dict)]
    focused_numbers = focus_pr_numbers(snapshot)
    focused_set = set(focused_numbers)
    focused_prs = [
        compact_focused_pr(pr)
        for pr in pulls
        if pr.get("number") in focused_set
    ]

    issue_numbers: set[int] = set()
    for pr in focused_prs:
        for number in pr.get("linked_issue_numbers", []):
            if type(number) is int:
                issue_numbers.add(number)
        for comment in pr.get("conversation_comments", []):
            if isinstance(comment, dict):
                issue_numbers.update(issue_refs(str(comment.get("body") or "")))

    focused_issues = []
    for issue in issues:
        number = issue.get("number")
        if type(number) is int and number in issue_numbers:
            detail = issue_index(issue)
            detail["body"] = str(issue.get("body") or "")[:FOCUSED_ISSUE_BODY_CHARS]
            focused_issues.append(detail)

    stable_context = {
        "readme": snapshot.get("readme") or "",
        "constitution_documents": snapshot.get("constitution_documents", []),
        "roadmap_documents": snapshot.get("roadmap_documents", []),
        "mission_index": mission_index(snapshot),
        "architecture_document_paths": snapshot.get("architecture_document_paths", []),
    }

    dynamic_context = {
        "repository": snapshot.get("repository"),
        "collected_at_utc": snapshot.get("collected_at_utc"),
        "source_main_sha": snapshot.get("main_sha"),
        "live_main_sha": snapshot.get("live_main_sha"),
        "tree_sha": snapshot.get("tree_sha"),
        "snapshot_consistent": snapshot.get("snapshot_consistent"),
        "branch_protection": snapshot.get("branch_protection"),
        "collection_errors": snapshot.get("collection_errors", []),
        "recent_commits": snapshot.get("recent_commits", [])[:12],
        "open_pull_request_index": [pr_index(pr) for pr in pulls],
        "focused_pull_requests": focused_prs,
        "open_issue_index": [issue_index(issue) for issue in issues],
        "focused_issues": focused_issues,
        "recent_main_action_runs": snapshot.get("recent_main_action_runs", [])[:10],
        "external_reviewer_state": compact_external(snapshot, focused_set),
        "context_policy": {
            "full_snapshot_preserved_as_cycle_artifact": True,
            "backlog_bodies_omitted_unless_focused": True,
            "missing_material_evidence_requires_evidence_request_not_inference": True,
        },
    }

    engineer_context = {
        "repository": dynamic_context["repository"],
        "source_main_sha": dynamic_context["source_main_sha"],
        "tree_sha": dynamic_context["tree_sha"],
        "branch_protection": dynamic_context["branch_protection"],
        "recent_commits": dynamic_context["recent_commits"],
        "open_pull_request_index": dynamic_context["open_pull_request_index"],
        "focused_pull_requests": focused_prs,
        "focused_issues": focused_issues,
        "architecture_document_paths": stable_context["architecture_document_paths"],
    }

    result = {
        "schema_version": MODEL_CONTEXT_SCHEMA,
        "main_sha": snapshot.get("main_sha"),
        "full_snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "stable_context": stable_context,
        "dynamic_context": dynamic_context,
        "engineer_context": engineer_context,
    }
    architect_chars = compact_json_chars(
        {"stable_context": stable_context, "dynamic_context": dynamic_context}
    )
    engineer_chars = compact_json_chars(engineer_context)
    result["metrics"] = {
        "full_snapshot_chars": len(snapshot_bytes.decode("utf-8")),
        "architect_context_chars": architect_chars,
        "engineer_context_chars": engineer_chars,
        "focused_pr_numbers": focused_numbers,
        "focused_issue_numbers": sorted(
            issue["number"]
            for issue in focused_issues
            if type(issue.get("number")) is int
        ),
    }
    if architect_chars > MAX_ARCHITECT_CONTEXT_CHARS:
        raise ValueError(
            f"architect model context exceeds bound: {architect_chars} > {MAX_ARCHITECT_CONTEXT_CHARS}"
        )
    if engineer_chars > MAX_ENGINEER_CONTEXT_CHARS:
        raise ValueError(
            f"engineer model context exceeds bound: {engineer_chars} > {MAX_ENGINEER_CONTEXT_CHARS}"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot = json.loads(snapshot_bytes.decode("utf-8"))
    context = build_context(snapshot, snapshot_bytes)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metrics = context["metrics"]
    print(
        "QORE_MODEL_CONTEXT full={} architect={} engineer={} focused_prs={} focused_issues={}".format(
            metrics["full_snapshot_chars"],
            metrics["architect_context_chars"],
            metrics["engineer_context_chars"],
            metrics["focused_pr_numbers"],
            metrics["focused_issue_numbers"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
