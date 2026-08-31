from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

import resume_after_agent_completion as resume_base
from build_preauthorized_review_chain import build_chain

CHAIN_MARKER_RE = re.compile(
    r"<!--\s*QORE-PREAUTHORIZED-REVIEW-CHAIN\s+id=(?P<id>\S+)\s+sha=(?P<sha>[0-9a-f]{64})\s+stage=(?P<stage>[A-Z_]+)\s*-->"
)


def validate_chain_callback(
    *,
    package_id: str,
    prompt_text: str,
    candidate_binding: Mapping[str, Any],
    source_architect_decision: Mapping[str, Any],
) -> dict[str, Any]:
    if source_architect_decision.get("schema_version") != "qore.architect.decision.v1":
        raise ValueError("source architect decision schema is invalid")
    if source_architect_decision.get("production_authority") is not False:
        raise ValueError("source architect decision attempted Production authority")
    contract = source_architect_decision.get("engineering_contract")
    if not isinstance(contract, Mapping) or contract.get("enabled") is not True:
        raise ValueError("source architect run did not preauthorize an engineering work unit")
    run_id = resume_base.reviewer_parent_run(package_id)
    chain = build_chain(
        candidate_binding=candidate_binding,
        engineering_contract=contract,
        source_architect_run_id=run_id,
    )
    markers = list(CHAIN_MARKER_RE.finditer(prompt_text))
    if len(markers) != 1:
        raise ValueError("reviewer prompt lacks one exact preauthorized-chain marker")
    marker = markers[0]
    if marker.group("id") != chain["chain_id"] or marker.group("sha") != chain["chain_sha256"]:
        raise ValueError("reviewer prompt preauthorized-chain identity does not recompute exactly")
    stage = marker.group("stage")
    matching = [
        row for row in chain["stages"]
        if isinstance(row, Mapping) and row.get("stage") == stage and row.get("package_id") == package_id
    ]
    if len(matching) != 1:
        raise ValueError("reviewer package/stage is not preauthorized by recomputed chain")
    return {
        "schema_version": "qore.preauthorized.review.callback.binding.v1",
        "source_architect_run_id": run_id,
        "chain_id": chain["chain_id"],
        "chain_sha256": chain["chain_sha256"],
        "stage": stage,
        "package_id": package_id,
        "candidate_id": chain["candidate_id"],
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate reviewer callback against recomputed preauthorized chain.")
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--candidate-binding", required=True, type=Path)
    parser.add_argument("--architect-decision", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    candidate = json.loads(args.candidate_binding.read_text(encoding="utf-8"))
    decision = json.loads(args.architect_decision.read_text(encoding="utf-8"))
    if not isinstance(candidate, Mapping) or not isinstance(decision, Mapping):
        raise SystemExit("candidate and architect decision must be JSON objects")
    result = validate_chain_callback(
        package_id=args.package_id,
        prompt_text=args.prompt.read_text(encoding="utf-8"),
        candidate_binding=candidate,
        source_architect_decision=decision,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
