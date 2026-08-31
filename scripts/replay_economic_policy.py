from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from build_economic_shadow_report import build_report


def replay_case(case: Mapping[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id", ""))
    if not case_id:
        raise ValueError("case_id must be non-empty")
    snapshot = case.get("snapshot")
    expected = case.get("expected")
    if not isinstance(snapshot, Mapping) or not isinstance(expected, Mapping):
        raise ValueError("replay case requires snapshot and expected objects")

    report = build_report(snapshot)
    checks: list[dict[str, Any]] = []

    def check(name: str, actual: Any, expected_value: Any) -> None:
        checks.append(
            {
                "name": name,
                "actual": actual,
                "expected": expected_value,
                "match": actual == expected_value,
            }
        )

    if "risk_tier" in expected:
        check("risk_tier", report["risk"]["tier"], expected["risk_tier"])
    if "review_stages" in expected:
        check("review_stages", report["review_plan"]["stages"], expected["review_stages"])
    if "fable_mode" in expected:
        actual_mode = report["fable"]["mode"] if report["fable"] is not None else None
        check("fable_mode", actual_mode, expected["fable_mode"])
    if "production_authority" in expected:
        check(
            "production_authority",
            report["production_authority"],
            expected["production_authority"],
        )

    return {
        "case_id": case_id,
        "checks": checks,
        "passed": all(item["match"] for item in checks),
        "report": report,
    }


def replay_corpus(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    results = [replay_case(case) for case in cases]
    failed = [result["case_id"] for result in results if not result["passed"]]
    return {
        "schema_version": "qore.economic.policy.replay.v1",
        "case_count": len(results),
        "passed_count": len(results) - len(failed),
        "failed_case_ids": failed,
        "all_passed": not failed,
        "results": results,
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay QORE economic routing policy without model calls.")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-all-pass", action="store_true")
    args = parser.parse_args()
    cases = json.loads(args.corpus.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise SystemExit("corpus must be a JSON array")
    result = replay_corpus(cases)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.require_all_pass and not result["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
