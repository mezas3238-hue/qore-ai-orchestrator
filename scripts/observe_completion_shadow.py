from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import resume_after_agent_completion as resume_base
import resume_after_agent_completion_v2 as resume_v2
from build_candidate_binding_api import build_candidate_binding_api
from build_codex_completion_event import build_codex_completion_event
from compact_packets_v2 import WorkUnitIdentity
from economic_control_plane import classify_risk_shadow, default_review_plan
from review_sequence_shadow import ReviewStageObservation, decide_review_sequence_shadow
from review_verdict_evidence import (
    VerdictClass,
    claude_review_from_artifact,
    deepseek_review_from_pr_reviews,
    evidence_to_json,
)

QORE_REPO = "mezas3238-hue/qore-core"
QORE_API = f"https://api.github.com/repos/{QORE_REPO}"


def _decode_request_at_run_head(token: str, repo: str, run_head_sha: str) -> dict[str, Any]:
    api = f"https://api.github.com/repos/{repo}"
    encoded = resume_base.urllib.parse.quote("requests/current.json", safe="/")
    payload = resume_base.api_json(token, api, f"/contents/{encoded}?ref={run_head_sha}")
    request = resume_base._decode_content(payload)
    if not isinstance(request, dict):
        raise resume_base.ResumeError("reviewer request evidence is invalid")
    return request


def _paged_list(token: str, api: str, path: str, *, max_pages: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in path else "?"
        payload = resume_base.api_json(token, api, f"{path}{sep}per_page=100&page={page}")
        if not isinstance(payload, list):
            raise resume_base.ResumeError("paged GitHub evidence is not a list")
        batch = [row for row in payload if isinstance(row, dict)]
        rows.extend(batch)
        if len(payload) < 100:
            return rows
    raise resume_base.ResumeError("paged GitHub evidence exceeded hard page bound")


def _source_tree(token: str, repository: str, source_sha: str) -> str:
    api = f"https://api.github.com/repos/{repository}"
    commit = resume_base.api_json(token, api, f"/git/commits/{source_sha}")
    if not isinstance(commit, Mapping):
        raise resume_base.ResumeError("source commit evidence is invalid")
    tree = commit.get("tree")
    tree_sha = tree.get("sha") if isinstance(tree, Mapping) else None
    if not isinstance(tree_sha, str) or resume_base.SHA_RE.fullmatch(tree_sha) is None:
        raise resume_base.ResumeError("source tree SHA is invalid")
    return tree_sha


def _codex_completion(
    *,
    event: dict[str, Any],
    event_name: str,
    orchestrator_token: str,
) -> dict[str, Any]:
    if event_name == "repository_dispatch":
        completion = resume_v2.parse_codex_repository_event(event, orchestrator_token)
    elif event_name == "workflow_run":
        completion = resume_base.parse_codex_event(event, orchestrator_token)
    else:
        raise resume_base.ResumeError("unsupported Codex shadow event")

    run_id = completion["run_id"]
    package_id = completion["package_id"]
    archive = resume_base.artifact_bytes(
        orchestrator_token,
        resume_base.ORCH_REPO,
        run_id,
        f"qore-codex-worker-{run_id}",
    )
    request = resume_base.extract_json(archive, "codex-request.json")
    result = resume_base.extract_json(archive, "codex-worker-result.json")
    usage = resume_base.extract_json(archive, "codex-worker-usage.json", required=False)
    publication = resume_base.extract_json(archive, "codex-publication.json", required=False)
    controller_qg = resume_base.extract_json(archive, "codex-controller-qg.json", required=False)
    assert request is not None and result is not None

    contract = request.get("engineering_contract")
    if not isinstance(contract, Mapping):
        raise resume_base.ResumeError("Codex request engineering contract is invalid")
    target_repository = contract.get("target_repository")
    contract_id = contract.get("contract_id")
    source_sha = request.get("source_main_sha")
    if not isinstance(target_repository, str) or not target_repository:
        raise resume_base.ResumeError("Codex target repository is invalid")
    if not isinstance(contract_id, str) or not contract_id:
        raise resume_base.ResumeError("Codex contract ID is invalid")
    if not isinstance(source_sha, str) or resume_base.SHA_RE.fullmatch(source_sha) is None:
        raise resume_base.ResumeError("Codex source SHA is invalid")

    work_unit = WorkUnitIdentity(
        repository=target_repository,
        source_main_sha=source_sha,
        source_tree_sha=_source_tree(orchestrator_token, target_repository, source_sha),
        contract_id=contract_id,
    )
    capsule = build_codex_completion_event(
        work_unit=work_unit,
        worker_result=result,
        worker_usage=usage,
    )

    candidate_binding: dict[str, Any] | None = None
    action: str
    reason: str
    if result.get("status") == "READY":
        if result.get("quality_gate_success") is not True or controller_qg is None:
            action = "EVIDENCE_REQUIRED"
            reason = "READY worker lacks complete independent controller Quality Gate evidence"
        elif publication is None:
            action = "EVIDENCE_REQUIRED"
            reason = "READY candidate has not been published/bound to an exact pull request"
        else:
            pr_number = publication.get("pull_request_number")
            candidate_head = publication.get("candidate_commit_sha")
            if type(pr_number) is not int or not isinstance(candidate_head, str):
                raise resume_base.ResumeError("Codex publication identity is invalid")
            candidate_binding = build_candidate_binding_api(
                token=orchestrator_token,
                repository=target_repository,
                pr_number=pr_number,
                expected_base=source_sha,
                expected_head=candidate_head,
            )
            action = "EXACT_QORE_CI_REQUIRED"
            reason = (
                "candidate is locally green and exactly bound; authenticated qore-core PR Quality Gate "
                "must still close before any independent reviewer"
            )
    else:
        changed_files = result.get("changed_files") or []
        if changed_files:
            action = "SOL_ADJUDICATION_REQUIRED"
            reason = "Codex stopped after producing a diff; semantic/engineering disposition is required"
        else:
            action = "ENGINEERING_RETRY_DECISION_REQUIRED"
            reason = "Codex stopped without a candidate; deterministic evidence should be supplied before another job"

    capsule["candidate_published"] = publication is not None
    return {
        "schema_version": "qore.completion.shadow.observation.v1",
        "event_actor": "CODEX",
        "package_id": package_id,
        "work_unit_id": work_unit.work_unit_id,
        "completion_capsule": capsule,
        "controller_qg_present": controller_qg is not None,
        "publication": publication,
        "candidate_binding": candidate_binding,
        "shadow_action": action,
        "reason": reason,
        "would_dispatch": False,
        "would_spend_api": False,
        "shadow_only": True,
        "production_authority": False,
    }


def _reviewer_completion(
    *,
    event: dict[str, Any],
    orchestrator_token: str,
    reviewer_token: str,
) -> dict[str, Any]:
    completion = resume_v2.parse_reviewer_event(event, reviewer_token)
    repo = completion["repo"]
    actor = completion["actor"]
    package_id = completion["package_id"]
    run_id = completion["run_id"]
    run_head = completion["run_head_sha"]
    request = _decode_request_at_run_head(reviewer_token, repo, run_head)
    if request.get("package_id") != package_id:
        raise resume_base.ResumeError("reviewer request/package binding changed")

    pr_number = request.get("pr_number")
    expected_base = request.get("expected_base")
    expected_head = request.get("expected_head")
    expected_synthetic = request.get("expected_synthetic")
    if type(pr_number) is not int or pr_number <= 0:
        raise resume_base.ResumeError("reviewer request pr_number is invalid")
    for label, value in (
        ("expected_base", expected_base),
        ("expected_head", expected_head),
        ("expected_synthetic", expected_synthetic),
    ):
        if not isinstance(value, str) or resume_base.SHA_RE.fullmatch(value) is None:
            raise resume_base.ResumeError(f"reviewer request {label} is invalid")

    binding = build_candidate_binding_api(
        token=orchestrator_token,
        repository=QORE_REPO,
        pr_number=pr_number,
        expected_base=expected_base,
        expected_head=expected_head,
        expected_synthetic=expected_synthetic,
    )
    files = _paged_list(orchestrator_token, QORE_API, f"/pulls/{pr_number}/files")
    changed_files = tuple(
        row["filename"] for row in files if isinstance(row.get("filename"), str)
    )
    semantic_change = any(
        path.startswith("src/qore/")
        or path.startswith("schemas/")
        or "contract" in path.lower()
        or "governance" in path.lower()
        for path in changed_files
    )
    risk = classify_risk_shadow(
        changed_files,
        semantic_change=semantic_change,
        release_or_production_sensitive=False,
    )

    if actor == "DEEPSEEK":
        reviews = _paged_list(orchestrator_token, QORE_API, f"/pulls/{pr_number}/reviews")
        verdict = deepseek_review_from_pr_reviews(
            reviews=reviews,
            package_id=package_id,
            expected_head=expected_head,
        )
        mode = str(request.get("review_mode") or "").lower()
        if mode == "expert":
            stage = "DEEPSEEK_EXPERT"
        elif mode == "coder":
            stage = "DEEPSEEK_CODER"
        else:
            raise resume_base.ResumeError("DeepSeek review_mode is invalid")
    elif actor == "CLAUDE_CODE":
        archive = resume_base.artifact_bytes(
            reviewer_token,
            repo,
            run_id,
            f"claude-{package_id}",
        )
        verdict = claude_review_from_artifact(
            archive_bytes=archive,
            package_id=package_id,
            expected_head=expected_head,
        )
        stage = "CLAUDE"
    else:
        raise resume_base.ResumeError("unsupported reviewer actor")

    observation = ReviewStageObservation(
        completed_stage=stage,
        verdict="CLEAN" if verdict.verdict is VerdictClass.CLEAN else verdict.verdict.value,
        run_completed=True,
        run_success=completion.get("conclusion") == "success",
        exact_candidate_unchanged=True,
        evidence_complete=verdict.verdict is not VerdictClass.AMBIGUOUS,
        anomaly_present=verdict.verdict in {VerdictClass.AMBIGUOUS, VerdictClass.MECHANICAL_FAILURE},
        finding_present=verdict.verdict is VerdictClass.FINDINGS,
        validation_blocked=verdict.verdict is VerdictClass.BLOCKED,
    )
    plan = default_review_plan(binding["candidate_id"], risk.tier)
    sequence = decide_review_sequence_shadow(plan=plan, observation=observation)

    return {
        "schema_version": "qore.completion.shadow.observation.v1",
        "event_actor": actor,
        "package_id": package_id,
        "review_stage": stage,
        "candidate_binding": binding,
        "changed_files": list(changed_files),
        "risk_tier": int(risk.tier),
        "risk_reasons": list(risk.reasons),
        "verdict_evidence": evidence_to_json(verdict),
        "review_sequence": {
            **asdict(sequence),
            "action": sequence.action.value,
        },
        "would_dispatch_next_reviewer_without_sol": sequence.action.value == "ADVANCE_PREAUTHORIZED",
        "would_call_sol": sequence.action.value in {
            "SOL_ADJUDICATION_REQUIRED",
            "COMPLETE_FOR_FINAL_SOL",
        },
        "would_dispatch": False,
        "would_spend_api": False,
        "shadow_only": True,
        "production_authority": False,
    }


def observe(event: dict[str, Any], event_name: str, *, orchestrator_token: str, reviewer_token: str) -> dict[str, Any]:
    if event_name == "repository_dispatch":
        payload = event.get("client_payload") if isinstance(event.get("client_payload"), dict) else {}
        if payload.get("actor") == "CODEX":
            return _codex_completion(
                event=event,
                event_name=event_name,
                orchestrator_token=orchestrator_token,
            )
        if not reviewer_token:
            raise resume_base.ResumeError("reviewer token is required for reviewer shadow observation")
        return _reviewer_completion(
            event=event,
            orchestrator_token=orchestrator_token,
            reviewer_token=reviewer_token,
        )
    if event_name == "workflow_run":
        return _codex_completion(
            event=event,
            event_name=event_name,
            orchestrator_token=orchestrator_token,
        )
    if event_name == "workflow_dispatch":
        return {
            "schema_version": "qore.completion.shadow.observation.v1",
            "event_actor": "NONE",
            "shadow_action": "DIAGNOSTIC_ONLY",
            "would_dispatch": False,
            "would_spend_api": False,
            "shadow_only": True,
            "production_authority": False,
        }
    raise resume_base.ResumeError(f"unsupported shadow event: {event_name or '<missing>'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe QORE agent completion with zero model calls.")
    parser.add_argument("--event", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    event = json.loads(args.event.read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise resume_base.ResumeError("event must be a JSON object")
    result = observe(
        event,
        os.environ.get("GITHUB_EVENT_NAME", ""),
        orchestrator_token=os.environ.get("GITHUB_TOKEN", "").strip(),
        reviewer_token=os.environ.get("QORE_REVIEWER_DISPATCH_TOKEN", "").strip(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "QORE_COMPLETION_SHADOW actor={} action={} next={} sol={} spend=false".format(
            result.get("event_actor"),
            result.get("shadow_action") or (result.get("review_sequence") or {}).get("action"),
            (result.get("review_sequence") or {}).get("next_stage"),
            result.get("would_call_sol", False),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (resume_base.ResumeError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"QORE_COMPLETION_SHADOW_ERROR: {exc}", file=resume_base.sys.stderr)
        raise SystemExit(31)
