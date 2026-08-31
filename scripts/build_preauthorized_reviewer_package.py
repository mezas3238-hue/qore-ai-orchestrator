from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import build_reviewer_package as legacy
from review_verdict_evidence import VerdictClass, deepseek_review_from_pr_reviews


def _stage(chain: Mapping[str, Any], stage_name: str) -> Mapping[str, Any]:
    stages = chain.get("stages")
    if not isinstance(stages, list):
        raise ValueError("review chain stages are invalid")
    matches = [row for row in stages if isinstance(row, Mapping) and row.get("stage") == stage_name]
    if len(matches) != 1:
        raise ValueError("requested review stage is not unique in chain")
    return matches[0]


def _reviews() -> list[dict[str, Any]]:
    payload = legacy.api_json("/pulls/{}/reviews".format(_CURRENT_PR), {"per_page": "100"})
    if not isinstance(payload, list):
        raise ValueError("pull request review evidence is invalid")
    return [row for row in payload if isinstance(row, dict)]


_CURRENT_PR = 0


def _require_previous_clean(chain: Mapping[str, Any], stage_name: str, head: str, reviews: list[dict[str, Any]]) -> None:
    if stage_name == "DEEPSEEK_EXPERT":
        return
    expert = _stage(chain, "DEEPSEEK_EXPERT")
    expert_evidence = deepseek_review_from_pr_reviews(
        reviews=reviews,
        package_id=str(expert["package_id"]),
        expected_head=head,
    )
    if expert_evidence.verdict is not VerdictClass.CLEAN:
        raise ValueError("DeepSeek Expert is not exact-head clean")
    if stage_name == "DEEPSEEK_CODER":
        return
    coder = _stage(chain, "DEEPSEEK_CODER")
    coder_evidence = deepseek_review_from_pr_reviews(
        reviews=reviews,
        package_id=str(coder["package_id"]),
        expected_head=head,
    )
    if coder_evidence.verdict is not VerdictClass.CLEAN:
        raise ValueError("DeepSeek Coder is not exact-head clean")


def build_stage_package(
    *,
    chain: Mapping[str, Any],
    stage_name: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if chain.get("schema_version") != "qore.preauthorized.review.chain.v1":
        raise ValueError("unexpected review chain schema")
    if chain.get("production_authority") is not False:
        raise ValueError("review chain attempted Production authority")
    if chain.get("reviewer_suppression") is not False or chain.get("final_sol_required") is not True:
        raise ValueError("current review chain policy invariants are invalid")
    if stage_name not in {"DEEPSEEK_EXPERT", "DEEPSEEK_CODER", "CLAUDE"}:
        raise ValueError("only external reviewer stages can build packages")

    candidate = chain.get("candidate")
    contract = chain.get("engineering_contract")
    if not isinstance(candidate, Mapping) or not isinstance(contract, Mapping):
        raise ValueError("review chain candidate/contract is invalid")
    pr_number = candidate.get("pull_request_number")
    if type(pr_number) is not int or pr_number <= 0:
        raise ValueError("review chain lacks a valid pull request number")
    expected = {
        "base": candidate.get("base_sha"),
        "head": candidate.get("head_sha"),
        "synthetic": candidate.get("synthetic_sha"),
    }
    base, head, synthetic = legacy.resolve_freeze(pr_number)
    if (base, head, synthetic) != (expected["base"], expected["head"], expected["synthetic"]):
        raise ValueError("live candidate no longer matches preauthorized review chain")
    qg = legacy.resolve_quality(head, synthetic)

    global _CURRENT_PR
    _CURRENT_PR = pr_number
    reviews = _reviews()
    _require_previous_clean(chain, stage_name, head, reviews)

    row = _stage(chain, stage_name)
    actor = str(row.get("actor") or "")
    review_kind = str(row.get("review_kind") or "")
    package_id = str(row.get("package_id") or "")
    objective = str(contract.get("objective") or "")
    scope = [str(x) for x in (contract.get("scope") or [])]
    acceptance = [str(x) for x in (contract.get("acceptance") or [])]
    forbidden = [str(x) for x in (contract.get("forbidden") or [])]
    if stage_name == "DEEPSEEK_EXPERT":
        foci = [
            "semantic contract violations and architecture boundary regressions",
            "recursive validation, exact runtime types, determinism, secret hygiene, and authority laundering",
            "adversarial cases not already covered by ordinary tests",
        ]
    elif stage_name == "DEEPSEEK_CODER":
        foci = [
            "implementation-level defects, edge cases, test insufficiency, and unsafe refactor behavior",
            "reproduce or falsify Expert concerns independently; Expert verdict is not authority",
        ]
    else:
        foci = [
            "independent cross-model falsification of the frozen candidate",
            "look for defects missed by both DeepSeek stages; prior verdicts are not authority",
        ]

    decision = {
        "source_main_sha": base,
        "status": "REVIEW_TASK",
        "decision": "Preauthorized exact-candidate independent review stage.",
        "roadmap_anchor": {"path": "preauthorized-review-chain", "work_package": contract.get("contract_id"), "reason": chain.get("chain_id")},
        "evidence": [
            {"kind": "review_chain_id", "value": str(chain.get("chain_id"))},
            {"kind": "review_chain_sha256", "value": str(chain.get("chain_sha256"))},
        ],
        "risk_gates": ["no reviewer suppression", "final Sol remains mandatory"],
    }
    review_contract = {
        "enabled": True,
        "contract_id": f"{contract.get('contract_id')}-{stage_name}",
        "pr_number": pr_number,
        "review_kind": review_kind,
        "objective": objective,
        "scope": scope,
        "adversarial_foci": foci,
        "acceptance": acceptance,
        "forbidden": forbidden,
    }
    prompt = legacy.build_prompt(
        decision=decision,
        contract=review_contract,
        base=base,
        head=head,
        synthetic=synthetic,
        qg=qg,
        package_id=package_id,
    )
    marker = (
        f"<!-- QORE-PREAUTHORIZED-REVIEW-CHAIN id={chain.get('chain_id')} "
        f"sha={chain.get('chain_sha256')} stage={stage_name} -->\n"
    )
    prompt = marker + prompt
    prompt_path = f"prompts/orchestrator/{package_id.lower()}.md"

    if actor == "DEEPSEEK":
        request = {
            "pr_number": pr_number,
            "package_id": package_id,
            "expected_base": base,
            "expected_head": head,
            "expected_synthetic": synthetic,
            "qg_summary": asdict(qg),
            "review_mode": "expert" if stage_name == "DEEPSEEK_EXPERT" else "coder",
            "prompt_path": prompt_path,
            "dispatch_nonce": f"CHAIN-{chain.get('chain_id')}-{stage_name}-{head}",
        }
        target_repo = "mezas3238-hue/qore-deepseek-reviewer"
    elif actor == "CLAUDE_CODE":
        qg_dict = asdict(qg)
        request = {
            "expected_base": base,
            "expected_head": head,
            "expected_synthetic": synthetic,
            "package_id": package_id,
            "pr_number": pr_number,
            "prompt_path": prompt_path,
            "qg": {
                "expected": {key: value for key, value in qg_dict.items() if key not in {"run_id", "job_id"}},
                "job_id": qg.job_id,
                "run_id": qg.run_id,
            },
        }
        target_repo = "mezas3238-hue/qore-claude-reviewer"
    else:
        raise ValueError("review chain actor is invalid")

    metadata = {
        "actor": actor,
        "target_repo": target_repo,
        "package_id": package_id,
        "prompt_path": prompt_path,
        "pr_number": pr_number,
        "base": base,
        "head": head,
        "synthetic": synthetic,
        "qg": asdict(qg),
        "preauthorized_review_chain_id": chain.get("chain_id"),
        "preauthorized_review_chain_sha256": chain.get("chain_sha256"),
        "stage": stage_name,
        "production_authority": False,
    }
    return prompt, request, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Build next exact preauthorized reviewer package.")
    parser.add_argument("--chain", required=True, type=Path)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--prompt-output", required=True, type=Path)
    parser.add_argument("--request-output", required=True, type=Path)
    parser.add_argument("--metadata-output", required=True, type=Path)
    args = parser.parse_args()
    chain = json.loads(args.chain.read_text(encoding="utf-8"))
    if not isinstance(chain, Mapping):
        raise SystemExit("review chain must be a JSON object")
    prompt, request, metadata = build_stage_package(chain=chain, stage_name=args.stage)
    for path in (args.prompt_output, args.request_output, args.metadata_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.prompt_output.write_text(prompt + "\n", encoding="utf-8")
    args.request_output.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.metadata_output.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
