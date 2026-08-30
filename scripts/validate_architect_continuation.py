#!/usr/bin/env python3
"""Validate Sol continuation semantics after strict schema decoding."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ALLOWED_STATUSES = {
    "ENGINEERING_TASK",
    "REVIEW_TASK",
    "WAITING_AGENT",
    "HUMAN_DECISION_REQUIRED",
    "RECONSTRUCTION_REQUIRED",
    "PROGRAM_COMPLETE",
}
ENGINEERING_REPOSITORIES = {
    "mezas3238-hue/qore-core",
    "mezas3238-hue/qore-deepseek-reviewer",
    "mezas3238-hue/qore-claude-reviewer",
    "mezas3238-hue/qore-ai-orchestrator",
}
WAIT_ACTORS = {"CLAUDE_CODE", "DEEPSEEK", "CODEX"}


def _disabled_engineering(contract: Any) -> bool:
    return (
        isinstance(contract, dict)
        and contract.get("enabled") is False
        and contract.get("contract_id") == ""
        and contract.get("target_repository") == ""
        and contract.get("objective") == ""
        and contract.get("scope") == []
        and contract.get("acceptance") == []
        and contract.get("required_tests") == []
        and contract.get("forbidden") == []
    )


def _disabled_review(contract: Any) -> bool:
    return (
        isinstance(contract, dict)
        and contract.get("enabled") is False
        and contract.get("contract_id") == ""
        and contract.get("pr_number") == 0
        and contract.get("review_kind") == "NONE"
        and contract.get("objective") == ""
        and contract.get("scope") == []
        and contract.get("adversarial_foci") == []
        and contract.get("acceptance") == []
        and contract.get("forbidden") == []
    )


def _disabled_wait(wait: Any) -> bool:
    return (
        isinstance(wait, dict)
        and wait.get("enabled") is False
        and wait.get("actor") == "NONE"
        and wait.get("package_id") == ""
        and wait.get("reason") == ""
    )


def _reviewer_for_actor(snapshot: dict[str, Any], actor: str) -> dict[str, Any] | None:
    external = snapshot.get("external_reviewer_state")
    if not isinstance(external, dict):
        return None
    key = "claude" if actor == "CLAUDE_CODE" else "deepseek" if actor == "DEEPSEEK" else None
    if key is None:
        return None
    reviewer = external.get(key)
    return reviewer if isinstance(reviewer, dict) else None


def _pending_run_observed(reviewer: dict[str, Any]) -> bool:
    control = reviewer.get("control_plane")
    if not isinstance(control, dict) or control.get("visibility") != "AVAILABLE":
        return False
    runs = control.get("recent_action_runs")
    if not isinstance(runs, list):
        return False
    return any(
        isinstance(run, dict) and run.get("status") in {"queued", "in_progress"}
        for run in runs
    )


def _validate_wait(snapshot: dict[str, Any], wait: dict[str, Any]) -> None:
    actor = wait.get("actor")
    package_id = wait.get("package_id")
    reason = wait.get("reason")
    if actor not in WAIT_ACTORS:
        raise ValueError("WAITING_AGENT requires Claude, DeepSeek or Codex")
    if not isinstance(package_id, str) or not package_id.strip():
        raise ValueError("WAITING_AGENT requires an exact non-empty package_id")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("WAITING_AGENT requires a concrete wait reason")

    # Codex is currently synchronous PLAN-ONLY. It cannot be a real wait boundary
    # until a separate asynchronous Codex worker publishes a pending job state.
    if actor == "CODEX":
        raise ValueError("Codex PLAN-ONLY is synchronous and cannot be a WAITING_AGENT state")

    reviewer = _reviewer_for_actor(snapshot, actor)
    if reviewer is None:
        raise ValueError("WAITING_AGENT reviewer state is unavailable")
    current = reviewer.get("current_request")
    if not isinstance(current, dict) or current.get("package_id") != package_id:
        raise ValueError("WAITING_AGENT package_id does not match reviewer current_request")
    if not _pending_run_observed(reviewer):
        raise ValueError("WAITING_AGENT requires an observed queued/in-progress reviewer run")


def validate(decision: dict[str, Any], snapshot: dict[str, Any]) -> None:
    status = decision.get("status")
    actor = decision.get("next_actor")
    engineering = decision.get("engineering_contract")
    review = decision.get("review_contract")
    wait = decision.get("wait_state")

    if status not in ALLOWED_STATUSES:
        raise ValueError(f"architect status is not an autonomous-continuation status: {status}")
    if decision.get("source_main_sha") != snapshot.get("main_sha"):
        raise ValueError("decision/snapshot main SHA mismatch")
    if decision.get("production_authority") is not False:
        raise ValueError("Production authority must remain false")
    if not isinstance(wait, dict):
        raise ValueError("wait_state is missing")

    if status == "ENGINEERING_TASK":
        if actor != "CODEX" or not isinstance(engineering, dict) or engineering.get("enabled") is not True:
            raise ValueError("ENGINEERING_TASK must route an enabled contract to CODEX")
        if engineering.get("target_repository") not in ENGINEERING_REPOSITORIES:
            raise ValueError("engineering target_repository is not authorized")
        if not _disabled_review(review) or not _disabled_wait(wait):
            raise ValueError("ENGINEERING_TASK cannot also review or wait")
        return

    if status == "REVIEW_TASK":
        if actor not in {"CLAUDE_CODE", "DEEPSEEK"}:
            raise ValueError("REVIEW_TASK must route to Claude Code or DeepSeek")
        if not isinstance(review, dict) or review.get("enabled") is not True:
            raise ValueError("REVIEW_TASK requires an enabled review contract")
        if not _disabled_engineering(engineering) or not _disabled_wait(wait):
            raise ValueError("REVIEW_TASK cannot also engineer or wait")
        return

    if status == "WAITING_AGENT":
        if actor != "NONE":
            raise ValueError("WAITING_AGENT must use next_actor=NONE because work was already dispatched")
        if not _disabled_engineering(engineering) or not _disabled_review(review):
            raise ValueError("WAITING_AGENT cannot enable new work contracts")
        if wait.get("enabled") is not True:
            raise ValueError("WAITING_AGENT requires wait_state.enabled=true")
        _validate_wait(snapshot, wait)
        return

    if status == "RECONSTRUCTION_REQUIRED":
        if actor != "SOL":
            raise ValueError("RECONSTRUCTION_REQUIRED must route internal continuation to SOL")
        if not _disabled_engineering(engineering) or not _disabled_review(review) or not _disabled_wait(wait):
            raise ValueError("RECONSTRUCTION_REQUIRED cannot enable work or wait")
        requests = decision.get("evidence_requests")
        if not isinstance(requests, list) or not any(isinstance(x, str) and x.strip() for x in requests):
            raise ValueError("RECONSTRUCTION_REQUIRED requires concrete evidence_requests")
        return

    if status == "HUMAN_DECISION_REQUIRED":
        if actor != "HUMAN":
            raise ValueError("HUMAN_DECISION_REQUIRED must route to HUMAN")
        if not _disabled_engineering(engineering) or not _disabled_review(review) or not _disabled_wait(wait):
            raise ValueError("human gate cannot also enable agent work")
        return

    if status == "PROGRAM_COMPLETE":
        if actor != "NONE":
            raise ValueError("PROGRAM_COMPLETE must use next_actor=NONE")
        if not _disabled_engineering(engineering) or not _disabled_review(review) or not _disabled_wait(wait):
            raise ValueError("PROGRAM_COMPLETE cannot enable work or wait")
        return

    raise AssertionError("unreachable status")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    try:
        validate(decision, snapshot)
    except ValueError as exc:
        raise SystemExit(f"ARCHITECT_CONTINUATION_INVALID: {exc}") from exc

    status = str(decision["status"])
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"status={status}\n")
            handle.write(f"actor={decision['next_actor']}\n")
            handle.write(
                "engineering_enabled={}\n".format(
                    str(decision["engineering_contract"]["enabled"]).lower()
                )
            )
            handle.write(
                "review_enabled={}\n".format(
                    str(decision["review_contract"]["enabled"]).lower()
                )
            )
    print(f"ARCHITECT_CONTINUATION_OK status={status} actor={decision['next_actor']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
