#!/usr/bin/env python3
"""Codex worker V5: V4 plus a deterministic terminal Quality Gate at budget edge.

V5 preserves V4's model surface, historical materialization, stateless/store=false
boundary, MAX_TURNS=16, and MAX_TOTAL_TOKENS=120000 input-equivalent units.

The only new behavior is controller-side and consumes zero additional model
calls: when the spend-equivalent budget is crossed immediately after a
successfully applied model patch, the controller may run the immutable full
QORE Quality Gate. A green, byte-stable candidate becomes an engineering READY
candidate for the normal independent controller QG and Sol/reviewer
adjudication. Any other condition remains BLOCKED.

This is not semantic approval, merge authority, GitHub authority, or Production
authority. A read/search/test action after the patch, a rejected patch, a QG
failure, a candidate mutation during QG, or a hard turn-limit stop cannot use
this fallback.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import run_codex_engineer_worker_v2 as v2
import run_codex_engineer_worker_v3 as v3
import run_codex_engineer_worker_v4 as v4

WORKER_VERSION = "v5"
PROMPT_CACHE_KEY = v4.PROMPT_CACHE_KEY
MAX_TURNS = v3.MAX_TURNS
MAX_TOTAL_TOKENS = v3.MAX_TOTAL_TOKENS
TERMINAL_QG_POLICY = "budget-after-successful-apply-patch-controller-qg-v1"

_ORIGINAL_DISPATCH_TOOL = v3.dispatch_tool
_ORIGINAL_BUDGET_BLOCK = v3.make_budget_block
_TERMINAL_QG_EVIDENCE: dict[str, Any] | None = None


class LocalToolsV5(v4.LocalToolsV4):
    """V4 tools with controller-only tracking of the latest bounded action."""

    def __init__(self, repo: Path, source_main_sha: str, allowed_reference_shas: tuple[str, ...]) -> None:
        super().__init__(repo, source_main_sha, allowed_reference_shas)
        self.last_bounded_action: str | None = None
        self.last_successful_patch_fingerprint: str | None = None


def dispatch_tool_v5(tools: LocalToolsV5, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Delegate to V3's exact tool surface while tracking terminal-QG eligibility."""
    try:
        result = _ORIGINAL_DISPATCH_TOOL(tools, name, args)
    except (v2.WorkerError, OSError, subprocess.TimeoutExpired):
        tools.last_bounded_action = f"{name}:error"
        tools.last_successful_patch_fingerprint = None
        raise

    if name == "apply_patch":
        applied = result.get("applied") if isinstance(result, dict) else None
        if applied is True:
            tools.last_bounded_action = "apply_patch"
            tools.last_successful_patch_fingerprint = v2.candidate_fingerprint(tools.repo)
        else:
            tools.last_bounded_action = "apply_patch:rejected"
            tools.last_successful_patch_fingerprint = None
    else:
        tools.last_bounded_action = name
        tools.last_successful_patch_fingerprint = None
    return result


def _safe_qg_evidence(
    *,
    before: str,
    after: str,
    changed: list[str],
    qg: dict[str, Any] | None,
    error: str | None = None,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    success = False
    run_number: int | None = None
    if isinstance(qg, dict):
        success = qg.get("success") is True
        value = qg.get("run_number")
        if type(value) is int and value >= 0:
            run_number = value
        results = qg.get("results")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                command = item.get("command")
                returncode = item.get("returncode")
                if isinstance(command, str) and type(returncode) is int:
                    commands.append({"command": command, "returncode": returncode})
    evidence: dict[str, Any] = {
        "policy": TERMINAL_QG_POLICY,
        "trigger": "budget_after_successful_apply_patch",
        "no_additional_model_call": True,
        "candidate_fingerprint_before": before,
        "candidate_fingerprint_after": after,
        "candidate_unchanged_by_qg": before == after,
        "changed_files": changed,
        "quality_gate_success": success,
        "quality_gate_run_number": run_number,
        "commands": commands,
        "production_authority": False,
    }
    if error:
        evidence["controller_error"] = error
    return evidence


def terminal_budget_result(
    repo: Path,
    source: str,
    contract_id: str,
    tools: v3.LocalToolsV3,
    turns: int,
    summary: str,
) -> dict[str, Any]:
    """Run zero-token terminal QG only at the exact successful-patch budget edge."""
    global _TERMINAL_QG_EVIDENCE
    _TERMINAL_QG_EVIDENCE = None

    if not isinstance(tools, LocalToolsV5):
        return _ORIGINAL_BUDGET_BLOCK(repo, source, contract_id, tools, turns, summary)
    if tools.last_bounded_action != "apply_patch":
        return _ORIGINAL_BUDGET_BLOCK(repo, source, contract_id, tools, turns, summary)
    before = v2.candidate_fingerprint(repo)
    changed = v2.changed_files(repo)
    if not changed or before != tools.last_successful_patch_fingerprint:
        return _ORIGINAL_BUDGET_BLOCK(repo, source, contract_id, tools, turns, summary)

    qg: dict[str, Any] | None = None
    controller_error: str | None = None
    try:
        qg = tools.run_quality_gate()
    except (v2.WorkerError, OSError, subprocess.TimeoutExpired) as exc:
        controller_error = f"{type(exc).__name__}: {exc}"
        tools.last_quality_success = False

    after = v2.candidate_fingerprint(repo)
    _TERMINAL_QG_EVIDENCE = _safe_qg_evidence(
        before=before,
        after=after,
        changed=changed,
        qg=qg,
        error=controller_error,
    )

    if (
        controller_error is None
        and isinstance(qg, dict)
        and qg.get("success") is True
        and before == after
        and tools.last_quality_success
    ):
        return v2.make_result(
            repo,
            source,
            contract_id,
            "READY",
            "Controller terminal Quality Gate certified the last successfully applied candidate at the model budget boundary.",
            [
                "The model budget was not increased and no additional model call was made.",
                "READY means engineering candidate ready for the normal independent controller QG and Sol/reviewer adjudication; it is not semantic approval or merge authority.",
                f"terminal_qg_policy={TERMINAL_QG_POLICY}",
            ],
            tools,
            turns,
        )

    tools.last_quality_success = False
    notes = [
        "The terminal controller Quality Gate did not certify a byte-stable green candidate.",
        "No additional model call was made and no candidate may be published as READY.",
        f"terminal_qg_policy={TERMINAL_QG_POLICY}",
    ]
    if controller_error:
        notes.append("The terminal QG controller raised an execution error; see bounded usage evidence.")
    elif before != after:
        notes.append("The candidate fingerprint changed while the Quality Gate was running.")
    return v2.make_result(
        repo,
        source,
        contract_id,
        "BLOCKED",
        "Codex worker reached the model budget after a patch, but the deterministic terminal Quality Gate did not certify the candidate.",
        notes,
        tools,
        turns,
    )


def _annotate_usage(path: Path) -> None:
    if not path.exists():
        return
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise v2.WorkerError("Codex usage artifact is not an object")
    value["worker_version"] = WORKER_VERSION
    value["terminal_qg_policy"] = TERMINAL_QG_POLICY
    value["terminal_qg_evidence"] = _TERMINAL_QG_EVIDENCE
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--charter", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--usage-output", required=True)
    args = parser.parse_args()

    global _TERMINAL_QG_EVIDENCE
    _TERMINAL_QG_EVIDENCE = None

    previous_argv = sys.argv[:]
    old_tools_class = v4.LocalToolsV4
    old_dispatch = v3.dispatch_tool
    old_budget_block = v3.make_budget_block
    old_version = v4.WORKER_VERSION
    try:
        v4.LocalToolsV4 = LocalToolsV5
        v3.dispatch_tool = dispatch_tool_v5
        v3.make_budget_block = terminal_budget_result
        v4.WORKER_VERSION = WORKER_VERSION
        sys.argv = [
            "run_codex_engineer_worker_v5.py",
            "--repo-dir",
            args.repo_dir,
            "--request",
            args.request,
            "--charter",
            args.charter,
            "--output",
            args.output,
            "--usage-output",
            args.usage_output,
        ]
        code = v4.main()
    finally:
        sys.argv = previous_argv
        v4.LocalToolsV4 = old_tools_class
        v3.dispatch_tool = old_dispatch
        v3.make_budget_block = old_budget_block
        v4.WORKER_VERSION = old_version

    _annotate_usage(Path(args.usage_output))
    print(
        "CODEX_ENGINEER_WORKER_V5_OK "
        f"terminal_qg_policy={TERMINAL_QG_POLICY} no_additional_model_call=true"
    )
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except v2.WorkerError as exc:
        print(f"CODEX_ENGINEER_WORKER_V5_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(8) from exc
