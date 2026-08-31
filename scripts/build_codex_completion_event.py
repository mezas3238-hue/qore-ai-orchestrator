from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from compact_packets_v2 import WorkUnitIdentity
from economic_control_plane import sha256_json


def build_codex_completion_event(
    *,
    work_unit: WorkUnitIdentity,
    worker_result: Mapping[str, Any],
    worker_usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if worker_result.get("schema_version") != "qore.codex.worker.result.v1":
        raise ValueError("unexpected Codex worker result schema")
    if worker_result.get("production_authority") is not False:
        raise ValueError("Codex worker result attempted Production authority")
    if worker_result.get("source_main_sha") != work_unit.source_main_sha:
        raise ValueError("Codex worker source_main_sha does not match work unit")
    if worker_result.get("contract_id") != work_unit.contract_id:
        raise ValueError("Codex worker contract_id does not match work unit")

    changed_files = worker_result.get("changed_files")
    if not isinstance(changed_files, list) or any(
        not isinstance(path, str) or not path for path in changed_files
    ):
        raise ValueError("changed_files must be an array of non-empty strings")
    status = worker_result.get("status")
    if status not in {"READY", "BLOCKED"}:
        raise ValueError("unsupported Codex worker status")
    qg_runs = worker_result.get("quality_gate_runs")
    qg_success = worker_result.get("quality_gate_success")
    turns = worker_result.get("turns")
    if type(qg_runs) is not int or qg_runs < 0:
        raise ValueError("quality_gate_runs must be a non-negative exact int")
    if type(qg_success) is not bool:
        raise ValueError("quality_gate_success must be exact bool")
    if type(turns) is not int or turns < 0:
        raise ValueError("turns must be a non-negative exact int")

    usage_summary: dict[str, Any] | None = None
    if worker_usage is not None:
        if worker_usage.get("model") is None:
            raise ValueError("worker usage must contain model")
        usage_summary = {
            "model": worker_usage.get("model"),
            "input_tokens": worker_usage.get("input_tokens"),
            "cached_tokens": worker_usage.get("cached_tokens"),
            "cache_write_tokens": worker_usage.get("cache_write_tokens"),
            "output_tokens": worker_usage.get("output_tokens"),
            "budget_tokens": worker_usage.get("budget_tokens"),
            "max_total_tokens": worker_usage.get("max_total_tokens"),
            "max_turns": worker_usage.get("max_turns"),
            "materialized_reference_sha": worker_usage.get("materialized_reference_sha"),
        }

    body = {
        "schema_version": "qore.codex.completion.event.v1",
        "work_unit_id": work_unit.work_unit_id,
        "source_main_sha": work_unit.source_main_sha,
        "contract_id": work_unit.contract_id,
        "status": status,
        "summary": worker_result.get("summary"),
        "changed_files": list(changed_files),
        "diff_sha256": worker_result.get("diff_sha256"),
        "quality_gate_runs": qg_runs,
        "quality_gate_success": qg_success,
        "turns": turns,
        "usage_summary": usage_summary,
        "candidate_published": False,
        "production_authority": False,
    }
    digest = sha256_json(body)
    body["event_sha256"] = digest
    body["event_id"] = f"QORE-CODEX-EVENT-{digest[:24]}"
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a compact Codex completion event capsule.")
    parser.add_argument("--work-unit", required=True, type=Path)
    parser.add_argument("--worker-result", required=True, type=Path)
    parser.add_argument("--worker-usage", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    work_value = json.loads(args.work_unit.read_text(encoding="utf-8"))
    raw = work_value.get("work_unit", work_value)
    work_unit = WorkUnitIdentity(**raw)
    result = json.loads(args.worker_result.read_text(encoding="utf-8"))
    usage = (
        json.loads(args.worker_usage.read_text(encoding="utf-8"))
        if args.worker_usage is not None
        else None
    )
    event = build_codex_completion_event(
        work_unit=work_unit,
        worker_result=result,
        worker_usage=usage,
    )
    args.output.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
