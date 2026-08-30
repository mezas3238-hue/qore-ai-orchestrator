#!/usr/bin/env python3
"""Retry-aware wrapper for bounded reviewer recovery dispatch.

The canonical trigger remains the source/evidence validator. At most three
recovery attempts are permitted for one source architect run. The third slot
exists only to migrate the run-4 recovery from failed job-log transport to
frozen structured QG evidence; every previous attempt must be terminal,
non-successful, and free of reviewer package/dispatch side effects.
"""

from __future__ import annotations

import time
from typing import Any

import trigger_reviewer_recovery as trigger

MAX_RECOVERY_ATTEMPTS = 3
RETRYABLE_TERMINAL_CONCLUSIONS = {
    "failure",
    "cancelled",
    "timed_out",
    "startup_failure",
    "action_required",
    "stale",
}


def matching_recoveries(token: str, source_run_id: int) -> list[dict[str, Any]]:
    payload = trigger.api_json(
        token,
        f"/actions/workflows/{trigger.RECOVERY_WORKFLOW}/runs?event=workflow_dispatch&per_page=50",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise trigger.RecoveryTriggerError("recovery workflow history is invalid")
    title = f"{trigger.RECOVERY_TITLE_PREFIX}{source_run_id}"
    matches = [
        run
        for run in payload["workflow_runs"]
        if isinstance(run, dict) and run.get("display_title") == title
    ]
    if len(matches) > MAX_RECOVERY_ATTEMPTS:
        raise trigger.RecoveryTriggerError("recovery retry cap history is inconsistent")
    return matches


def attempt_has_side_effect_evidence(token: str, run_id: int) -> bool:
    archive = trigger._source_artifact(token, run_id)
    return (
        trigger._extract_json(archive, "reviewer-package.json", required=False) is not None
        or trigger._extract_json(archive, "reviewer-dispatch.json", required=False) is not None
    )


def retry_eligible(token: str, source_run_id: int) -> tuple[bool, list[dict[str, Any]]]:
    matches = matching_recoveries(token, source_run_id)
    if not matches:
        return True, matches

    for run in matches:
        run_id = run.get("id")
        if type(run_id) is not int or run_id <= 0:
            raise trigger.RecoveryTriggerError("existing recovery run ID is invalid")
        if run.get("status") != "completed":
            return False, matches
        conclusion = run.get("conclusion")
        if conclusion == "success":
            return False, matches
        if conclusion not in RETRYABLE_TERMINAL_CONCLUSIONS:
            raise trigger.RecoveryTriggerError("existing recovery conclusion is not safely retryable")
        if attempt_has_side_effect_evidence(token, run_id):
            return False, matches

    if len(matches) >= MAX_RECOVERY_ATTEMPTS:
        raise trigger.RecoveryTriggerError("reviewer recovery retry cap exhausted")
    return True, matches


def dispatch_recovery_retry_aware(token: str, source_run_id: int, expected_head: str) -> int | None:
    eligible, _existing = retry_eligible(token, source_run_id)
    if not eligible:
        return None

    before_payload = trigger.api_json(
        token,
        f"/actions/workflows/{trigger.RECOVERY_WORKFLOW}/runs?event=workflow_dispatch&per_page=50",
    )
    if not isinstance(before_payload, dict) or not isinstance(before_payload.get("workflow_runs"), list):
        raise trigger.RecoveryTriggerError("could not snapshot recovery history before dispatch")
    before_ids = {
        run.get("id")
        for run in before_payload["workflow_runs"]
        if isinstance(run, dict) and type(run.get("id")) is int
    }
    status = trigger.api_status(
        token,
        f"/actions/workflows/{trigger.RECOVERY_WORKFLOW}/dispatches",
        {
            "ref": "main",
            "inputs": {
                "source_architect_run_id": str(source_run_id),
                "expected_source_head_sha": expected_head,
            },
        },
    )
    if status != 204:
        raise trigger.RecoveryTriggerError(f"reviewer recovery dispatch failed with HTTP {status}")

    title = f"{trigger.RECOVERY_TITLE_PREFIX}{source_run_id}"
    for _attempt in range(20):
        time.sleep(2)
        payload = trigger.api_json(
            token,
            f"/actions/workflows/{trigger.RECOVERY_WORKFLOW}/runs?event=workflow_dispatch&per_page=50",
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            continue
        matches = [
            run
            for run in payload["workflow_runs"]
            if isinstance(run, dict)
            and type(run.get("id")) is int
            and run.get("id") not in before_ids
            and run.get("display_title") == title
            and run.get("head_branch") == "main"
        ]
        if len(matches) == 1:
            return matches[0]["id"]
        if len(matches) > 1:
            raise trigger.RecoveryTriggerError("recovery dispatch created multiple matching runs")
    raise trigger.RecoveryTriggerError("recovery dispatch returned 204 but no exact run was observed")


def main() -> int:
    trigger.dispatch_recovery = dispatch_recovery_retry_aware
    return trigger.main()


if __name__ == "__main__":
    raise SystemExit(main())
