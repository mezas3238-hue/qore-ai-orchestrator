from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from compact_packets_v2 import WorkUnitIdentity


def build_work_unit_from_existing(
    *,
    model_context: Mapping[str, Any],
    architect_decision: Mapping[str, Any],
) -> dict[str, Any]:
    if model_context.get("schema_version") != "qore.model.context.v1":
        raise ValueError("unexpected model context schema")
    if architect_decision.get("schema_version") != "qore.architect.decision.v1":
        raise ValueError("unexpected architect decision schema")
    if architect_decision.get("production_authority") is not False:
        raise ValueError("architect decision attempted Production authority")

    dynamic = model_context.get("dynamic_context")
    if not isinstance(dynamic, Mapping):
        raise ValueError("model context dynamic_context is missing")
    if dynamic.get("snapshot_consistent") is not True:
        raise ValueError("source snapshot is not exact/live-main consistent")

    source_main = dynamic.get("source_main_sha")
    live_main = dynamic.get("live_main_sha")
    tree_sha = dynamic.get("tree_sha")
    decision_main = architect_decision.get("source_main_sha")
    if source_main != live_main or source_main != decision_main:
        raise ValueError("source/live/decision main SHA mismatch")

    contract = architect_decision.get("engineering_contract")
    if not isinstance(contract, Mapping) or contract.get("enabled") is not True:
        raise ValueError("enabled engineering contract is required")
    contract_id = contract.get("contract_id")
    target_repository = contract.get("target_repository")
    if not isinstance(contract_id, str) or not contract_id.strip():
        raise ValueError("contract_id is required")
    if not isinstance(target_repository, str) or not target_repository.strip():
        raise ValueError("target_repository is required")

    work_unit = WorkUnitIdentity(
        repository=target_repository,
        source_main_sha=str(source_main),
        source_tree_sha=str(tree_sha),
        contract_id=contract_id,
    )
    return {
        "schema_version": "qore.work.unit.shadow.v1",
        "work_unit_id": work_unit.work_unit_id,
        "work_unit": asdict(work_unit),
        "architect_status": architect_decision.get("status"),
        "next_actor": architect_decision.get("next_actor"),
        "contract": dict(contract),
        "evidence_requests": list(architect_decision.get("evidence_requests") or []),
        "risk_gates": list(architect_decision.get("risk_gates") or []),
        "shadow_only": True,
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Project existing QORE artifacts into a V2 work unit.")
    parser.add_argument("--model-context", required=True, type=Path)
    parser.add_argument("--architect-decision", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    context = json.loads(args.model_context.read_text(encoding="utf-8"))
    decision = json.loads(args.architect_decision.read_text(encoding="utf-8"))
    result = build_work_unit_from_existing(
        model_context=context,
        architect_decision=decision,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
