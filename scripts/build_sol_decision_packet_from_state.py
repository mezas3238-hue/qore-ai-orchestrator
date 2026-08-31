from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from compact_packets_v2 import (
    SolSubjectKind,
    WorkUnitIdentity,
    build_sol_decision_packet_v2,
)
from economic_control_plane import CandidateIdentity, classify_risk_shadow


def build_packet(subject: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    schema = subject.get("schema_version")
    work_unit: WorkUnitIdentity | None = None
    candidate: CandidateIdentity | None = None
    if schema == "qore.work.unit.shadow.v1":
        raw = subject.get("work_unit")
        if not isinstance(raw, Mapping):
            raise ValueError("work unit subject is invalid")
        work_unit = WorkUnitIdentity(**dict(raw))
        kind = SolSubjectKind.WORK_UNIT
    elif schema in {"qore.candidate.binding.v1", "qore.candidate.binding.api.v1"}:
        candidate = CandidateIdentity(
            repository=str(subject["repository"]),
            base_sha=str(subject["base_sha"]),
            head_sha=str(subject["head_sha"]),
            tree_sha=str(subject["tree_sha"]),
            synthetic_sha=str(subject["synthetic_sha"]),
            production_authority=False,
        )
        kind = SolSubjectKind.CANDIDATE
    else:
        raise ValueError("unsupported Sol packet subject schema")

    if state.get("production_authority", False) is not False:
        raise ValueError("controller state attempted Production authority")
    changed_files = state.get("changed_files", [])
    if not isinstance(changed_files, list) or any(not isinstance(path, str) for path in changed_files):
        raise ValueError("changed_files must be an array of strings")
    risk = classify_risk_shadow(
        changed_files,
        semantic_change=state.get("semantic_change", False),
        release_or_production_sensitive=state.get("release_or_production_sensitive", False),
    )
    contract = state.get("active_contract")
    if not isinstance(contract, Mapping):
        raise ValueError("active_contract must be an object")
    findings = state.get("findings", {})
    if not isinstance(findings, Mapping):
        raise ValueError("findings must be an object")
    qg = state.get("qg_summary", {})
    review = state.get("review_summary", {})
    diff = state.get("diff_summary", {})
    if not all(isinstance(value, Mapping) for value in (qg, review, diff)):
        raise ValueError("diff/qg/review summaries must be objects")
    questions = state.get("semantic_questions", [])
    slices = state.get("source_slices", [])
    transitions = state.get("allowed_transitions", [])
    if not isinstance(questions, list) or not isinstance(slices, list) or not isinstance(transitions, list):
        raise ValueError("questions/slices/transitions must be arrays")

    return build_sol_decision_packet_v2(
        subject_kind=kind,
        work_unit=work_unit,
        candidate=candidate,
        risk=risk,
        workflow_state=str(state.get("workflow_state") or ""),
        last_event=str(state.get("last_event") or ""),
        decision_required=str(state.get("decision_required") or ""),
        active_contract=dict(contract),
        semantic_questions=[str(item) for item in questions],
        changed_files=changed_files,
        diff_summary=dict(diff),
        findings={str(key): list(value) for key, value in findings.items()},
        qg_summary=dict(qg),
        review_summary=dict(review),
        source_slices=list(slices),
        budget_remaining_usd=float(state.get("budget_remaining_usd", 0.0)),
        allowed_transitions=[str(item) for item in transitions],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact compact Sol decision packet from deterministic state.")
    parser.add_argument("--subject", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    subject = json.loads(args.subject.read_text(encoding="utf-8"))
    state = json.loads(args.state.read_text(encoding="utf-8"))
    if not isinstance(subject, Mapping) or not isinstance(state, Mapping):
        raise SystemExit("subject and state must be JSON objects")
    packet = build_packet(subject, state)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
