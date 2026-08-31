from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import run_codex_engineer_worker_v2 as v2
import run_codex_engineer_worker_v6 as v6

PATH_RE = v6.PATH_RE


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def paths_in(values: list[str]) -> set[str]:
    found: set[str] = set()
    for text in values:
        found.update(match.group("path") for match in PATH_RE.finditer(text))
    return found


def patch_paths(contract: Mapping[str, Any]) -> tuple[str, ...]:
    """Only objective/scope can grant model write scope.

    acceptance, required_tests and forbidden text are evidence/constraints only;
    merely naming a test there never grants permission to edit it.
    """
    values = _strings(contract.get("objective")) + _strings(contract.get("scope"))
    return tuple(sorted(paths_in(values)))


def evidence_paths(contract: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("objective", "scope", "acceptance", "required_tests", "forbidden"):
        values.extend(_strings(contract.get(key)))
    return tuple(sorted(paths_in(values)))


def hardened_initial_evidence(
    repo: Path,
    contract: Mapping[str, Any],
    materialization: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    readable = set(evidence_paths(contract))
    writable = set(patch_paths(contract))
    materialized_paths: set[str] = set()
    if materialization is not None:
        changed = materialization.get("changed_files")
        if isinstance(changed, list):
            materialized_paths.update(path for path in changed if isinstance(path, str))
            readable.update(materialized_paths)
            writable.update(materialized_paths)

    if len(writable) > v2.MAX_CHANGED_FILES:
        raise v2.WorkerError("Codex V6 write allowlist exceeds changed-file hard bound")
    if not writable:
        raise v2.WorkerError("Codex V6 requires explicit objective/scope write paths or exact materialized paths")

    files: list[dict[str, Any]] = []
    missing: list[str] = []
    total = 0
    for rel in sorted(readable):
        target = v2.safe_path(repo, rel, must_exist=False)
        if not target.exists():
            missing.append(rel)
            continue
        if not target.is_file() or target.is_symlink():
            raise v2.WorkerError(f"evidence path is not a regular file: {rel}")
        item = v6._bounded_text_file(repo, rel)
        rendered = len(json.dumps(item, separators=(",", ":"), ensure_ascii=False))
        if total + rendered > v6.MAX_EVIDENCE_CHARS:
            break
        files.append(item)
        total += rendered

    return {
        "files": files,
        "missing_contract_paths": missing,
        "current_diff": v6._candidate_diff(repo),
        "materialization": dict(materialization) if materialization is not None else None,
        "evidence_chars": total,
        "readable_evidence_paths": sorted(readable),
        "model_write_allowlist": sorted(writable),
        "policy": "objective_scope_write__acceptance_tests_read_only_v1",
    }, tuple(sorted(writable))
