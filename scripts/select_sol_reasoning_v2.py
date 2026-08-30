#!/usr/bin/env python3
"""Choose a bounded GPT-5.6 Sol reasoning effort from focused canonical QORE state."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TIERS = ("medium", "high", "xhigh", "max")
RANK = {name: index for index, name in enumerate(TIERS)}
OUTPUT_LIMITS = {"medium": 4000, "high": 5500, "xhigh": 8000, "max": 10000}
ACTIVE_PR_WINDOW_DAYS = 7
ISSUE_REF_RE = re.compile(r"(?<![\w/])#(?P<number>\d{1,6})\b")

XHIGH_TERMS = {
    "architecture", "architectural", "invariant", "semantic", "cross-provider",
    "cross provider", "provider-neutral", "provider neutral", "failover", "fencing",
    "reconciliation", "state machine", "identity", "governance", "authority boundary",
    "compatibility", "red team",
}
MAX_TERMS = {
    "security boundary", "security incident", "secret leak", "secret exposure",
    "credential leak", "credential exposure", "credential authority", "productive credential",
    "production activation", "real capital", "real-money", "real money", "split-brain",
    "split brain", "bypass risk", "invariant contradiction", "architectural contradiction",
    "architecture contradiction",
}
FAILURE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _issue_refs(text: str) -> set[int]:
    return {int(match.group("number")) for match in ISSUE_REF_RE.finditer(text)}


def _focused_pr_numbers(snapshot: dict[str, Any]) -> set[int]:
    pulls = [pr for pr in snapshot.get("open_pull_requests", []) if isinstance(pr, dict)]
    open_numbers = {
        pr.get("number") for pr in pulls
        if type(pr.get("number")) is int and pr.get("number") > 0
    }
    focused: set[int] = set()
    external = snapshot.get("external_reviewer_state")
    if isinstance(external, dict):
        for reviewer_name in ("deepseek", "claude"):
            reviewer = external.get(reviewer_name)
            current = reviewer.get("current_request") if isinstance(reviewer, dict) else None
            number = current.get("pr_number") if isinstance(current, dict) else None
            if type(number) is int and number in open_numbers:
                focused.add(number)
    if focused:
        return focused
    pulls_sorted = sorted(pulls, key=lambda pr: str(pr.get("updated_at") or ""), reverse=True)
    for pr in pulls_sorted[:2]:
        number = pr.get("number")
        if type(number) is int and number > 0:
            focused.add(number)
    return focused


def _focused_signal_text(snapshot: dict[str, Any]) -> str:
    pulls = [pr for pr in snapshot.get("open_pull_requests", []) if isinstance(pr, dict)]
    issues = [issue for issue in snapshot.get("open_issues", []) if isinstance(issue, dict)]
    focused_numbers = _focused_pr_numbers(snapshot)
    parts: list[str] = []
    linked_issues: set[int] = set()
    for pr in pulls:
        if pr.get("number") not in focused_numbers:
            continue
        parts.append(str(pr.get("title") or ""))
        body = str(pr.get("body") or "")
        parts.append(body[:4000])
        linked_issues.update(_issue_refs(body))
        for comment in pr.get("conversation_comments", []):
            if isinstance(comment, dict):
                comment_body = str(comment.get("body") or "")
                parts.append(comment_body[:3000])
                linked_issues.update(_issue_refs(comment_body))
    if not focused_numbers and not pulls and issues:
        newest = sorted(issues, key=lambda issue: str(issue.get("updated_at") or ""), reverse=True)[0]
        parts.append(str(newest.get("title") or ""))
        parts.append(str(newest.get("body") or "")[:4000])
        labels = newest.get("labels")
        if isinstance(labels, list):
            parts.extend(str(label) for label in labels)
    for issue in issues:
        if issue.get("number") in linked_issues:
            parts.append(str(issue.get("title") or ""))
            parts.append(str(issue.get("body") or "")[:4000])
            labels = issue.get("labels")
            if isinstance(labels, list):
                parts.extend(str(label) for label in labels)
    return "\n".join(parts).lower()


def _focused_critical_text(snapshot: dict[str, Any]) -> str:
    pulls = [pr for pr in snapshot.get("open_pull_requests", []) if isinstance(pr, dict)]
    issues = [issue for issue in snapshot.get("open_issues", []) if isinstance(issue, dict)]
    focused_numbers = _focused_pr_numbers(snapshot)
    parts: list[str] = []
    linked_issues: set[int] = set()
    for pr in pulls:
        if pr.get("number") not in focused_numbers:
            continue
        parts.append(str(pr.get("title") or ""))
        body = str(pr.get("body") or "")
        parts.append(body[:4000])
        linked_issues.update(_issue_refs(body))
    if not focused_numbers and not pulls and issues:
        newest = sorted(issues, key=lambda issue: str(issue.get("updated_at") or ""), reverse=True)[0]
        parts.append(str(newest.get("title") or ""))
        parts.append(str(newest.get("body") or "")[:4000])
        labels = newest.get("labels")
        if isinstance(labels, list):
            parts.extend(str(label) for label in labels)
    for issue in issues:
        if issue.get("number") in linked_issues:
            parts.append(str(issue.get("title") or ""))
            parts.append(str(issue.get("body") or "")[:4000])
            labels = issue.get("labels")
            if isinstance(labels, list):
                parts.extend(str(label) for label in labels)
    external = snapshot.get("external_reviewer_state")
    if isinstance(external, dict):
        claude = external.get("claude")
        review = claude.get("review") if isinstance(claude, dict) else None
        if isinstance(review, dict) and review.get("verdict") in {"FINDINGS", "MECHANICAL_FAILURE"}:
            parts.append(str(review.get("text") or "")[:4000])
    return "\n".join(parts).lower()


def _active_pr_count(snapshot: dict[str, Any]) -> int:
    pulls = [pr for pr in snapshot.get("open_pull_requests", []) if isinstance(pr, dict)]
    collected = _parse_time(snapshot.get("collected_at_utc")) or datetime.now(timezone.utc)
    count = 0
    for pr in pulls:
        updated = _parse_time(pr.get("updated_at"))
        if updated is None:
            continue
        age_days = (collected - updated).total_seconds() / 86400
        if 0 <= age_days <= ACTIVE_PR_WINDOW_DAYS:
            count += 1
    return count


def _reviewer_signals(snapshot: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    external = snapshot.get("external_reviewer_state")
    if isinstance(external, dict):
        claude = external.get("claude")
        if isinstance(claude, dict):
            review = claude.get("review")
            if isinstance(review, dict):
                verdict = review.get("verdict")
                if verdict in {"FINDINGS", "AMBIGUOUS", "MECHANICAL_FAILURE"}:
                    signals.append(f"Claude return state is {verdict}")
    for pr in snapshot.get("open_pull_requests", []):
        if not isinstance(pr, dict):
            continue
        for review in pr.get("reviews", []):
            if not isinstance(review, dict):
                continue
            body = str(review.get("body") or "")
            if "QORE-DEEPSEEK-REVIEW" not in body:
                continue
            if "VALIDACIÓN NO OK" in body:
                signals.append("DeepSeek exact-PR review reports VALIDACIÓN NO OK")
            elif "EVIDENCIA INSUFICIENTE" in body or "VALIDATION BLOCKED" in body:
                signals.append("DeepSeek exact-PR review reports insufficient/blocked evidence")
    return signals


def choose_effort(snapshot: dict[str, Any], requested_mode: str) -> dict[str, Any]:
    if requested_mode != "auto":
        if requested_mode not in RANK:
            raise ValueError("requested reasoning mode is invalid")
        return {"requested_mode": requested_mode, "selected_effort": requested_mode,
                "max_output_tokens": OUTPUT_LIMITS[requested_mode],
                "reasons": ["explicit workflow override"]}

    selected = "medium"
    reasons: list[str] = ["routine reconstruction baseline"]
    pulls = [x for x in snapshot.get("open_pull_requests", []) if isinstance(x, dict)]
    issues = [x for x in snapshot.get("open_issues", []) if isinstance(x, dict)]
    runs = [x for x in snapshot.get("recent_main_action_runs", []) if isinstance(x, dict)]
    failed_runs = [run for run in runs if str(run.get("conclusion") or "").lower() in FAILURE_CONCLUSIONS]
    if pulls or issues or failed_runs:
        selected = "high"
        reasons.append("material live work or CI state requires non-routine coordination")
    active_prs = _active_pr_count(snapshot)
    if active_prs >= 3:
        selected = "xhigh"
        reasons.append("three or more recently active PRs increase live integration ambiguity")
    reviewer_signals = _reviewer_signals(snapshot)
    if reviewer_signals and RANK[selected] < RANK["xhigh"]:
        selected = "xhigh"
        reasons.append("independent reviewer finding/ambiguity requires deeper adjudication: " + "; ".join(reviewer_signals[:3]))
    signal_text = _focused_signal_text(snapshot)
    critical_text = _focused_critical_text(snapshot)
    xhigh_hits = sorted(term for term in XHIGH_TERMS if term in signal_text)
    max_hits = sorted(term for term in MAX_TERMS if term in critical_text)
    if xhigh_hits and RANK[selected] < RANK["xhigh"]:
        selected = "xhigh"
        reasons.append("focused cross-cutting architecture signal: " + ", ".join(xhigh_hits[:5]))
    if max_hits:
        selected = "max"
        reasons.append("focused critical safety/governance signal: " + ", ".join(max_hits[:5]))
    if snapshot.get("snapshot_consistent") is not True or snapshot.get("collection_errors"):
        selected = "max"
        reasons.append("snapshot inconsistency exists; caller must fail closed before model spend")
    return {"requested_mode": "auto", "selected_effort": selected,
            "max_output_tokens": OUTPUT_LIMITS[selected], "reasons": reasons}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--mode", required=True, choices=("auto", *TIERS))
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    policy = choose_effort(snapshot, args.mode)
    Path(args.output).write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"effort={policy['selected_effort']}\n")
            handle.write(f"max_output_tokens={policy['max_output_tokens']}\n")
    print("SOL_REASONING_POLICY effort={} max_output_tokens={} reasons={}".format(
        policy["selected_effort"], policy["max_output_tokens"], "; ".join(policy["reasons"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
