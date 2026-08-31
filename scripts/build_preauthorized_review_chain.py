from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from economic_control_plane import sha256_json

STAGES = ("DEEPSEEK_EXPERT", "DEEPSEEK_CODER", "CLAUDE", "SOL_FINAL")


def build_chain(
    *,
    candidate_binding: Mapping[str, Any],
    engineering_contract: Mapping[str, Any],
    source_architect_run_id: int,
) -> dict[str, Any]:
    if candidate_binding.get("schema_version") not in {
        "qore.candidate.binding.v1",
        "qore.candidate.binding.api.v1",
    }:
        raise ValueError("unexpected candidate binding schema")
    if candidate_binding.get("production_authority", False) is not False:
        raise ValueError("candidate binding attempted Production authority")
    if engineering_contract.get("enabled") is not True:
        raise ValueError("enabled engineering contract is required")
    if type(source_architect_run_id) is not int or source_architect_run_id <= 0:
        raise ValueError("source_architect_run_id must be a positive exact int")
    contract_id = engineering_contract.get("contract_id")
    if not isinstance(contract_id, str) or not contract_id:
        raise ValueError("engineering contract_id is required")

    main_short = str(candidate_binding["base_sha"])[:12]
    stage_rows = [
        {
            "stage": "DEEPSEEK_EXPERT",
            "actor": "DEEPSEEK",
            "review_kind": "DEEPSEEK_EXPERT",
            "package_id": f"QORE-SOL-{main_short}-DS-EXPERT-R{source_architect_run_id}",
        },
        {
            "stage": "DEEPSEEK_CODER",
            "actor": "DEEPSEEK",
            "review_kind": "DEEPSEEK_CODER",
            "package_id": f"QORE-SOL-{main_short}-DS-CODER-R{source_architect_run_id}",
        },
        {
            "stage": "CLAUDE",
            "actor": "CLAUDE_CODE",
            "review_kind": "CLAUDE_TECHNICAL",
            "package_id": f"QORE-SOL-{main_short}-CLAUDE-R{source_architect_run_id}",
        },
        {
            "stage": "SOL_FINAL",
            "actor": "SOL",
            "review_kind": "FINAL_ADJUDICATION",
            "package_id": None,
        },
    ]
    body = {
        "schema_version": "qore.preauthorized.review.chain.v1",
        "candidate_id": candidate_binding.get("candidate_id"),
        "candidate": {
            key: candidate_binding[key]
            for key in ("repository", "pull_request_number", "base_sha", "head_sha", "tree_sha", "synthetic_sha")
            if key in candidate_binding
        },
        "source_architect_run_id": source_architect_run_id,
        "engineering_contract": dict(engineering_contract),
        "stages": stage_rows,
        "advance_rule": (
            "advance only after exact completed-success reviewer run, unchanged BASE/HEAD/TREE/SYNTHETIC, "
            "complete evidence, and unambiguous clean verdict"
        ),
        "invalidate_rule": "any candidate HEAD/freeze mutation invalidates the entire remaining chain",
        "finding_rule": "finding, blocked validation, ambiguity, or anomaly requires Sol adjudication",
        "final_sol_required": True,
        "reviewer_suppression": False,
        "production_authority": False,
    }
    digest = sha256_json(body)
    body["chain_sha256"] = digest
    body["chain_id"] = f"QORE-REVIEW-CHAIN-{digest[:24]}"
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description="Build immutable preauthorized reviewer chain.")
    parser.add_argument("--candidate-binding", required=True, type=Path)
    parser.add_argument("--engineering-contract", required=True, type=Path)
    parser.add_argument("--source-architect-run-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    candidate = json.loads(args.candidate_binding.read_text(encoding="utf-8"))
    contract = json.loads(args.engineering_contract.read_text(encoding="utf-8"))
    if not isinstance(candidate, Mapping) or not isinstance(contract, Mapping):
        raise SystemExit("candidate and contract must be JSON objects")
    result = build_chain(
        candidate_binding=candidate,
        engineering_contract=contract,
        source_architect_run_id=args.source_architect_run_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
