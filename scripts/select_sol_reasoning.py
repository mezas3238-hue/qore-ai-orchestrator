#!/usr/bin/env python3
"""Choose a bounded GPT-5.6 Sol reasoning effort from canonical QORE state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

TIERS = ("medium", "high", "xhigh", "max")
RANK = {name: index for index, name in enumerate(TIERS)}
OUTPUT_LIMITS = {"medium": 5000, "high": 7000, "xhigh": 9000, "max": 12000}

XHIGH_TERMS = {
    "architecture",
    "architectural",
    "invariant",
    "semantic",
    "cross-provider",
    "cross provider",
    "provider-neutral",
    "provider neutral",
    "failover",
    "fencing",
    "reconciliation",
    "state machine",
    "identity",
    "governance",
    "authority boundary",
    "compatibility",
    "red team",
}
MAX_TERMS = {
    "security",
    "secret leak",
    "credential",
    "production activation",
    "real capital",
    "real-money",
    "real money",
    "split-brain",
    "split brain",
    "bypass risk",
    "invariant contradiction",
    "architectural contradiction",
    "architecture contradiction",
}
FAILURE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}


def _signal_text(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    for pr in snapshot.get("open_pull_requests", []):
        if isinstance(pr, dict):
            parts.append(str(pr.get("title") or ""))
    for issue in snapshot.get("open_issues", []):
        if isinstance(issue, dict):
            parts.append(str(issue.get("title") or ""))
            labels = issue.get("labels")
            if isinstance(labels, list):
                parts.extend(str(label) for label in labels)
    for commit in snapshot.get("recent_commits", [])[:8]:
        if isinstance(commit, dict):
            parts.append(str(commit.get("subject") or ""))
    return "\n".join(parts).lower()


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
        return {
            "requested_mode": requested_mode,
            "selected_effort": requested_mode,
            "max_output_tokens": OUTPUT_LIMITS[requested_mode],
            "reasons": ["explicit workflow override"],
        }

    selected = "medium"
    reasons: list[str] = ["routine reconstruction baseline"]

    pulls = [x for x in snapshot.get("open_pull_requests", []) if isinstance(x, dict)]
    issues = [x for x in snapshot.get("open_issues", []) if isinstance(x, dict)]
    runs = [x for x in snapshot.get("recent_main_action_runs", []) if isinstance(x, dict)]

    failed_runs = [
        run for run in runs if str(run.get("conclusion") or "").lower() in FAILURE_CONCLUSIONS
    ]
    if pulls or len(issues) > 1 or failed_runs:
        selected = "high"
        reasons.append("material live work or CI anomaly requires non-routine coordination")

    if len(pulls) >= 3:
        selected = "xhigh"
        reasons.append("multiple simultaneous open PRs increase integration ambiguity")

    reviewer_signals = _reviewer_signals(snapshot)
    if reviewer_signals and RANK[selected] < RANK["xhigh"]:
        selected = "xhigh"
        reasons.append("independent reviewer finding/ambiguity requires deeper adjudication: " + "; ".join(reviewer_signals[:3]))

    signal_text = _signal_text(snapshot)
    max_hits = sorted(term for term in MAX_TERMS if term in signal_text)
    xhigh_hits = sorted(term for term in XHIGH_TERMS if term in signal_text)

    if xhigh_hits and RANK[selected] < RANK["xhigh"]:
        selected = "xhigh"
        reasons.append("cross-cutting architecture signal: " + ", ".join(xhigh_hits[:5]))
    if max_hits:
        selected = "max"
        reasons.append("critical safety/governance signal: " + ", ".join(max_hits[:5]))

    if snapshot.get("snapshot_consistent") is not True or snapshot.get("collection_errors"):
        selected = "max"
        reasons.append("snapshot inconsistency exists; caller must fail closed before model spend")

    return {
        "requested_mode": "auto",
        "selected_effort": selected,
        "max_output_tokens": OUTPUT_LIMITS[selected],
        "reasons": reasons,
    }


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

    print(
        "SOL_REASONING_POLICY effort={} max_output_tokens={} reasons={}".format(
            policy["selected_effort"],
            policy["max_output_tokens"],
            "; ".join(policy["reasons"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
