#!/usr/bin/env python3
"""Activation-safe Codex V6 wrapper with read/write scope separation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import codex_v6_scope_policy as scope_policy
import run_codex_engineer_worker_v2 as v2
import run_codex_engineer_worker_v6 as v6

WORKER_VERSION = "v6-hardened"
ALLOWED_ACTIVATION_MODES = {"CONTROLLED_VALIDATION", "LIMITED_LIVE"}


def execute_hardened(*, key: str, repo: Path, request: dict, charter: str):
    original = v6._initial_evidence
    try:
        v6._initial_evidence = scope_policy.hardened_initial_evidence
        final, usage = v6.execute_v6(key=key, repo=repo, request=request, charter=charter)
    finally:
        v6._initial_evidence = original
    usage["worker_version"] = WORKER_VERSION
    usage["scope_policy"] = "objective_scope_write__acceptance_tests_read_only_v1"
    usage["production_authority"] = False
    return final, usage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--charter", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--usage-output", required=True)
    args = parser.parse_args()

    mode = os.environ.get("QORE_CODEX_V6_MODE", "").strip()
    if mode not in ALLOWED_ACTIVATION_MODES:
        print("QORE_CODEX_V6_MODE must be CONTROLLED_VALIDATION or LIMITED_LIVE.", file=sys.stderr)
        return 2
    key = os.environ.get("OPENAI_CODEX_API_KEY", "").strip()
    if not key:
        print("OPENAI_CODEX_API_KEY is not configured.", file=sys.stderr)
        return 2
    repo = Path(args.repo_dir).resolve()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise v2.WorkerError("engineering request must be a JSON object")
    charter = Path(args.charter).read_text(encoding="utf-8")
    final, usage = execute_hardened(key=key, repo=repo, request=request, charter=charter)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.usage_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.usage_output).write_text(json.dumps(usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "CODEX_ENGINEER_WORKER_V6_HARDENED_OK status={} calls={} changed_files={}".format(
            final["status"], usage["model_calls"], len(final["changed_files"])
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (v2.WorkerError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"CODEX_ENGINEER_WORKER_V6_HARDENED_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(8)
