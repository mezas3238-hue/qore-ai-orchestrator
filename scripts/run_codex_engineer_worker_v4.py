#!/usr/bin/env python3
"""Codex worker V4: V3 loop plus deterministic historical candidate materialization.

V4 keeps V3's stateless/store=false cache-stable model loop and model tool
surface. Before model spend it may materialize one exact historical delta only
when the immutable architect objective explicitly requires starting/checking out
that historical candidate. The reference must be the single exact 40-hex SHA
already allowlisted by the contract and a direct descendant of source main.

Materialization is a controller operation, not model authority: no arbitrary
checkout, fetch, shell, network, GitHub credential, merge, commit, push,
reviewer or Production authority is added.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import run_codex_engineer_worker_v2 as v2
import run_codex_engineer_worker_v3 as v3

WORKER_VERSION = "v4"
PROMPT_CACHE_KEY = "qore-codex-engineer-worker-v4"
REFERENCE_MATERIALIZATION_POLICY = "explicit-objective-single-allowlisted-descendant-v1"
MATERIALIZATION_MARKERS = (
    "cumulative replacement candidate",
    "checking out exact",
    "check out exact",
    "checkout exact",
    "starting from exact",
    "start from exact",
)

_AUTO_REFERENCE_SHA: str | None = None
_MATERIALIZATION_EVIDENCE: dict[str, Any] | None = None


def required_materialized_reference(contract: dict[str, Any], source_main_sha: str) -> str | None:
    """Resolve a materialization request only from explicit objective language."""
    objective = contract.get("objective")
    if not isinstance(objective, str):
        raise v2.WorkerError("engineering contract objective is invalid")
    if not any(marker in objective.casefold() for marker in MATERIALIZATION_MARKERS):
        return None
    references = v3.contract_reference_shas(contract, source_main_sha)
    if len(references) != 1:
        raise v2.WorkerError(
            "historical materialization objective requires exactly one contract-allowlisted reference SHA"
        )
    return references[0]


def _commit_exists(repo: Path, sha: str) -> None:
    result = v2.run_process(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo, timeout=60)
    if result.returncode != 0:
        raise v2.WorkerError("historical reference commit is not present in the local clone")


def _reference_blob(repo: Path, reference_sha: str, path: str) -> str | None:
    exists = v2.run_process(
        ["git", "cat-file", "-e", f"{reference_sha}:{path}"], cwd=repo, timeout=60
    )
    if exists.returncode != 0:
        return None
    value = v2.git(repo, "rev-parse", f"{reference_sha}:{path}").strip()
    if not v2.SHA_RE.fullmatch(value):
        raise v2.WorkerError("historical reference blob identity is invalid")
    return value


def materialize_reference_delta(
    repo: Path,
    source_main_sha: str,
    reference_sha: str,
    allowed_reference_shas: tuple[str, ...],
) -> dict[str, Any]:
    """Apply the exact source->reference delta and verify resulting file bytes."""
    if reference_sha not in frozenset(allowed_reference_shas):
        raise v2.WorkerError("historical reference is not allowlisted by the engineering contract")
    if v2.SHA_RE.fullmatch(reference_sha) is None:
        raise v2.WorkerError("historical reference SHA is invalid")
    if v2.git(repo, "rev-parse", "HEAD").strip() != source_main_sha:
        raise v2.WorkerError("historical materialization source HEAD moved")
    if v2.changed_files(repo):
        raise v2.WorkerError("historical materialization requires a clean source working tree")
    _commit_exists(repo, reference_sha)

    merge_base = v2.git(repo, "merge-base", source_main_sha, reference_sha).strip()
    if merge_base != source_main_sha:
        raise v2.WorkerError("historical reference is not descended exactly from source main")

    patch = v2.git(
        repo,
        "diff",
        "--binary",
        "--no-ext-diff",
        source_main_sha,
        reference_sha,
        "--",
        timeout=60,
    )
    if "old mode " in patch or "new mode " in patch:
        raise v2.WorkerError("historical materialization refuses file-mode changes")
    paths = v2.validate_patch_paths(patch)
    expected_paths = sorted(
        path
        for path in v2.git(
            repo,
            "diff",
            "--name-only",
            source_main_sha,
            reference_sha,
            "--",
            timeout=60,
        ).splitlines()
        if path
    )
    if paths != expected_paths:
        raise v2.WorkerError("historical reference patch paths do not match exact changed-file set")

    check = v2.run_process(
        ["git", "apply", "--check", "--binary", "--whitespace=error-all", "-"],
        cwd=repo,
        input_text=patch,
        timeout=60,
    )
    if check.returncode != 0:
        raise v2.WorkerError(
            "historical reference delta failed exact apply preflight: " + v2.clip(check.stdout, 8000)
        )
    applied = v2.run_process(
        ["git", "apply", "--binary", "--whitespace=error-all", "-"],
        cwd=repo,
        input_text=patch,
        timeout=60,
    )
    if applied.returncode != 0:
        raise v2.WorkerError(
            "historical reference delta failed after preflight: " + v2.clip(applied.stdout, 8000)
        )

    actual_paths = v2.changed_files(repo)
    if actual_paths != expected_paths:
        raise v2.WorkerError("materialized working tree does not match historical changed-file set")
    if len(actual_paths) > v2.MAX_CHANGED_FILES:
        raise v2.WorkerError("materialized working tree exceeds changed-file hard bound")

    for rel in expected_paths:
        expected_blob = _reference_blob(repo, reference_sha, rel)
        target = v2.safe_path(repo, rel, must_exist=False)
        if expected_blob is None:
            if target.exists():
                raise v2.WorkerError(f"historical deletion did not materialize exactly: {rel}")
            continue
        if not target.is_file() or target.is_symlink():
            raise v2.WorkerError(f"materialized reference path is not a regular file: {rel}")
        actual_blob = v2.git(repo, "hash-object", "--", rel).strip()
        if actual_blob != expected_blob:
            raise v2.WorkerError(f"materialized file bytes do not match historical reference: {rel}")

    return {
        "source_main_sha": source_main_sha,
        "reference_sha": reference_sha,
        "merge_base_sha": merge_base,
        "changed_files": actual_paths,
        "delta_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
    }


class LocalToolsV4(v3.LocalToolsV3):
    """V3 model tools over a controller-materialized cumulative candidate."""

    def __init__(self, repo: Path, source_main_sha: str, allowed_reference_shas: tuple[str, ...]) -> None:
        super().__init__(repo, source_main_sha, allowed_reference_shas)
        global _MATERIALIZATION_EVIDENCE
        if _AUTO_REFERENCE_SHA is not None:
            _MATERIALIZATION_EVIDENCE = materialize_reference_delta(
                repo,
                source_main_sha,
                _AUTO_REFERENCE_SHA,
                allowed_reference_shas,
            )
            self.last_quality_success = False


def _augment_charter(original: str, reference_sha: str | None) -> str:
    if reference_sha is None:
        return original
    return (
        original
        + "\n\n## V4 controller materialization protocol\n"
        + "The deterministic controller has already materialized the exact historical reference "
        + reference_sha
        + " as the working-tree delta over exact source_main_sha after proving merge-base equality, "
        + "changed-file bounds, and exact reference blob bytes. Do NOT reconstruct, fetch, checkout, "
        + "or re-apply that historical delta. Treat the current git diff as the cumulative baseline. "
        + "Implement only the incremental corrections required by the architect contract. Inspect the "
        + "smallest relevant helpers/tests, patch promptly, run the required targeted test(s), then the "
        + "full Quality Gate. reference_diff remains read-only comparison evidence only.\n"
    )


def _annotate_usage(path: Path, reference_sha: str | None) -> None:
    if not path.exists():
        return
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise v2.WorkerError("Codex usage artifact is not an object")
    value["worker_version"] = WORKER_VERSION
    value["prompt_cache_key"] = PROMPT_CACHE_KEY
    value["reference_materialization_policy"] = REFERENCE_MATERIALIZATION_POLICY
    value["materialized_reference_sha"] = reference_sha
    value["materialization_evidence"] = _MATERIALIZATION_EVIDENCE
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--charter", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--usage-output", required=True)
    args = parser.parse_args()

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise v2.WorkerError("engineering request is not an object")
    source = request.get("source_main_sha")
    contract = request.get("engineering_contract")
    if not isinstance(source, str) or v2.SHA_RE.fullmatch(source) is None or not isinstance(contract, dict):
        raise v2.WorkerError("engineering request source/contract shape is invalid")
    reference_sha = required_materialized_reference(contract, source)

    global _AUTO_REFERENCE_SHA, _MATERIALIZATION_EVIDENCE
    _AUTO_REFERENCE_SHA = reference_sha
    _MATERIALIZATION_EVIDENCE = None

    original_charter = Path(args.charter).read_text(encoding="utf-8")
    augmented = _augment_charter(original_charter, reference_sha)
    usage_path = Path(args.usage_output)
    usage_path.parent.mkdir(parents=True, exist_ok=True)

    previous_argv = sys.argv[:]
    old_tools_class = v3.LocalToolsV3
    old_version = v3.WORKER_VERSION
    old_cache_key = v3.PROMPT_CACHE_KEY
    try:
        v3.LocalToolsV3 = LocalToolsV4
        v3.WORKER_VERSION = WORKER_VERSION
        v3.PROMPT_CACHE_KEY = PROMPT_CACHE_KEY
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(augmented)
            charter_path = Path(handle.name)
        try:
            sys.argv = [
                "run_codex_engineer_worker_v4.py",
                "--repo-dir",
                args.repo_dir,
                "--request",
                args.request,
                "--charter",
                str(charter_path),
                "--output",
                args.output,
                "--usage-output",
                args.usage_output,
            ]
            code = v3.main()
        finally:
            charter_path.unlink(missing_ok=True)
    finally:
        sys.argv = previous_argv
        v3.LocalToolsV3 = old_tools_class
        v3.WORKER_VERSION = old_version
        v3.PROMPT_CACHE_KEY = old_cache_key
        _AUTO_REFERENCE_SHA = None

    _annotate_usage(usage_path, reference_sha)
    print(
        "CODEX_ENGINEER_WORKER_V4_OK "
        f"materialized_reference={reference_sha or 'NONE'} policy={REFERENCE_MATERIALIZATION_POLICY}"
    )
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except v2.WorkerError as exc:
        print(f"CODEX_ENGINEER_WORKER_V4_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(7) from exc
