#!/usr/bin/env python3
"""Evaluate whether one bounded Sol retry at a higher reasoning effort is justified."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

TIERS = ("medium", "high", "xhigh", "max")
RANK = {name: index for index, name in enumerate(TIERS)}
OUTPUT_LIMITS = {"medium": 5000, "high": 7000, "xhigh": 9000, "max": 12000}

# Only active critical conditions may force max. Generic words such as
# "production" or "credential" are intentionally absent: QORE decisions repeat
# closed/prohibited boundaries on every cycle, and those protective statements
# must not create a paid verification retry by themselves.
MAX_ACTIVE_TERMS = {
    "security incident",
    "critical security",
    "secret leak",
    "credential leak",
    "credential exposed",
    "exposed credential",
    "compromised credential",
    "production activation request",
    "production activation requested",
    "production enabled",
    "production active",
    "real capital exposure",
    "real-capital exposure",
    "real money exposure",
    "real-money exposure",
    "split-brain",
    "split brain",
    "invariant contradiction",
    "architectural contradiction",
    "architecture contradiction",
    "bypass risk",
    "risk bypass",
}
XHIGH_ACTIVE_TERMS = {
    "failover",
    "fencing",
    "reconciliation",
    "cross-provider",
    "cross provider",
    "provider-neutral",
    "provider neutral",
    "state machine",
    "governance contradiction",
    "identity contradiction",
    "compatibility contradiction",
    "reviewer disagreement",
}


def _higher(current: str, candidate: str) -> str:
    if candidate not in RANK:
        return current
    return candidate if RANK[candidate] > RANK[current] else current


def _active_risk_hits(decision: dict[str, Any]) -> tuple[list[str], list[str]]:
    risk_text = "\n".join(str(x) for x in decision.get("risk_gates", [])).lower()
    max_hits = sorted(term for term in MAX_ACTIVE_TERMS if term in risk_text)
    xhigh_hits = sorted(term for term in XHIGH_ACTIVE_TERMS if term in risk_text)
    return max_hits, xhigh_hits


def choose_escalation(decision: dict[str, Any], current: str) -> dict[str, Any]:
    if current not in RANK:
        raise ValueError("invalid current reasoning effort")

    target = current
    reasons: list[str] = []
    assessment = decision.get("reasoning_assessment")
    if isinstance(assessment, dict) and assessment.get("escalation_requested") is True:
        requested = str(assessment.get("target_effort") or current)
        new_target = _higher(target, requested)
        if new_target != target:
            target = new_target
            reasons.append(
                "Sol requested a higher effort: "
                + str(assessment.get("reason") or "unspecified")
            )

    status = decision.get("status")
    if status == "HUMAN_DECISION_REQUIRED":
        target = "max"
        reasons.append(
            "human gate requires maximum verification before surfacing recommendation"
        )
    elif status == "RECONSTRUCTION_REQUIRED" and RANK[target] < RANK["xhigh"]:
        target = "xhigh"
        reasons.append("reconstruction failure deserves one deeper verification pass")

    max_hits, xhigh_hits = _active_risk_hits(decision)
    if max_hits:
        target = "max"
        reasons.append("active critical risk gate: " + ", ".join(max_hits[:5]))
    elif xhigh_hits and RANK[target] < RANK["xhigh"]:
        target = "xhigh"
        reasons.append("active cross-cutting risk gate: " + ", ".join(xhigh_hits[:5]))

    escalate = RANK[target] > RANK[current]
    return {
        "current_effort": current,
        "escalate": escalate,
        "target_effort": target,
        "max_output_tokens": OUTPUT_LIMITS[target],
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", required=True)
    parser.add_argument("--current", required=True, choices=TIERS)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    result = choose_escalation(decision, args.current)
    Path(args.output).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"escalate={'true' if result['escalate'] else 'false'}\n")
            handle.write(f"target_effort={result['target_effort']}\n")
            handle.write(f"max_output_tokens={result['max_output_tokens']}\n")

    print(
        "SOL_ESCALATION escalate={} target={} reasons={}".format(
            result["escalate"],
            result["target_effort"],
            "; ".join(result["reasons"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
