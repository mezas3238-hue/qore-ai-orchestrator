#!/usr/bin/env python3
"""Build an immutable Codex worker request from a Sol engineering decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_TARGETS = {"mezas3238-hue/qore-core"}


def package_id(source: str, contract_id: str) -> str:
    digest = hashlib.sha256(contract_id.encode("utf-8")).hexdigest()[:16]
    return f"QORE-CODEX-{source[:12]}-{digest}"


def build(decision: dict[str, Any], orchestrator_run_id: str) -> dict[str, Any]:
    if decision.get("schema_version") != "qore.architect.decision.v1":
        raise ValueError("unexpected architect decision schema")
    if decision.get("status") != "ENGINEERING_TASK" or decision.get("next_actor") != "CODEX":
        raise ValueError("decision does not authorize a Codex engineering task")
    source = decision.get("source_main_sha")
    if not isinstance(source, str) or not SHA_RE.fullmatch(source):
        raise ValueError("source_main_sha is invalid")
    contract = decision.get("engineering_contract")
    if not isinstance(contract, dict) or contract.get("enabled") is not True:
        raise ValueError("enabled engineering contract is required")
    target = contract.get("target_repository")
    if target not in ALLOWED_TARGETS:
        raise ValueError(f"Codex worker target is not enabled in this rollout: {target}")
    contract_id = contract.get("contract_id")
    if not isinstance(contract_id, str) or not contract_id.strip():
        raise ValueError("contract_id is required")
    if decision.get("production_authority") is not False:
        raise ValueError("architect decision attempted Production authority")
    return {
        "schema_version": "qore.codex.engineering.request.v1",
        "package_id": package_id(source, contract_id),
        "source_main_sha": source,
        "architect_run_id": str(orchestrator_run_id),
        "engineering_contract": contract,
        "production_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", required=True)
    parser.add_argument("--orchestrator-run-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    request = build(decision, args.orchestrator_run_id)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "CODEX_REQUEST_OK package={} contract={} target={} main={}".format(
            request["package_id"],
            request["engineering_contract"]["contract_id"],
            request["engineering_contract"]["target_repository"],
            request["source_main_sha"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
