#!/usr/bin/env python3
"""Rehydrate a failed Autonomous V2 review decision into a trusted recovery parent run."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ORCH_REPO = "mezas3238-hue/qore-ai-orchestrator"
API = f"https://api.github.com/repos/{ORCH_REPO}"
SOURCE_WORKFLOW_NAME = "QORE Architect autonomous V2"
SOURCE_WORKFLOW_PATH = ".github/workflows/qore-architect-autonomous-v2.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USER_AGENT = "qore-ai-orchestrator/1.0"
MAX_ZIP_BYTES = 4_000_000
MAX_JSON_BYTES = 700_000
ALLOWED_FILES = {
    "architect-decision.json",
    "architect-decision-initial.json",
    "architect-decision-before-reconstruction.json",
    "qore-state.json",
    "sol-usage.json",
    "sol-usage-initial.json",
    "sol-escalation.json",
    "sol-reasoning-policy.json",
}


class RecoveryPrepareError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def api_json(token: str, path: str) -> Any:
    request = urllib.request.Request(API + path, headers=_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RecoveryPrepareError(f"GitHub API {path} failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RecoveryPrepareError(f"GitHub API {path} failed: {type(exc).__name__}") from exc


def download_artifact(token: str, artifact_id: int) -> bytes:
    request = urllib.request.Request(f"{API}/actions/artifacts/{artifact_id}/zip", headers=_headers(token))
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=45)
        data = response.read(MAX_ZIP_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise RecoveryPrepareError(f"artifact download failed with HTTP {exc.code}") from exc
        location = exc.headers.get("Location")
        if not location:
            raise RecoveryPrepareError("artifact redirect lacks Location") from exc
        try:
            with urllib.request.urlopen(location, timeout=60) as redirected:
                data = redirected.read(MAX_ZIP_BYTES + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as redirected_exc:
            raise RecoveryPrepareError(
                f"signed artifact download failed: {type(redirected_exc).__name__}"
            ) from redirected_exc
    if len(data) > MAX_ZIP_BYTES:
        raise RecoveryPrepareError("source architect artifact exceeds hard ZIP size bound")
    return data


def validate_source_run(run: dict[str, Any], run_id: int, expected_head: str) -> None:
    if run.get("id") != run_id:
        raise RecoveryPrepareError("source architect run ID mismatch")
    if run.get("name") != SOURCE_WORKFLOW_NAME or run.get("path") != SOURCE_WORKFLOW_PATH:
        raise RecoveryPrepareError("source is not canonical Autonomous V2")
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main":
        raise RecoveryPrepareError("source architect origin is not trusted")
    if run.get("head_sha") != expected_head:
        raise RecoveryPrepareError("source architect HEAD mismatch")
    if run.get("status") != "completed" or run.get("conclusion") != "failure":
        raise RecoveryPrepareError("recovery source must be a completed failed run")


def source_artifact(token: str, run_id: int) -> tuple[bytes, dict[str, Any]]:
    payload = api_json(token, f"/actions/runs/{run_id}/artifacts?per_page=100")
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise RecoveryPrepareError("source architect artifact list is invalid")
    name = f"qore-architect-v2-{run_id}"
    matches = [
        item
        for item in payload["artifacts"]
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("expired") is False
        and type(item.get("id")) is int
    ]
    if len(matches) != 1:
        raise RecoveryPrepareError(f"expected one non-expired {name}; found {len(matches)}")
    return download_artifact(token, matches[0]["id"]), matches[0]


def extract_allowed(archive_bytes: bytes, output_dir: Path) -> set[str]:
    extracted: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                basename = path.name
                if basename not in ALLOWED_FILES:
                    continue
                if info.is_dir() or info.file_size > MAX_JSON_BYTES:
                    raise RecoveryPrepareError(f"invalid recovery source member {basename}")
                if basename in extracted:
                    raise RecoveryPrepareError(f"duplicate recovery source member {basename}")
                raw = archive.read(info)
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise RecoveryPrepareError(f"invalid JSON in {basename}") from exc
                if not isinstance(value, dict):
                    raise RecoveryPrepareError(f"{basename} is not a JSON object")
                (output_dir / basename).write_text(
                    json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                extracted.add(basename)
    except RecoveryPrepareError:
        raise
    except (zipfile.BadZipFile, KeyError) as exc:
        raise RecoveryPrepareError("source architect artifact is not a valid ZIP") from exc
    return extracted


def validate_rehydrated(output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {"architect-decision.json", "qore-state.json", "sol-usage-initial.json", "sol-usage.json"}
    missing = [name for name in sorted(required) if not (output_dir / name).is_file()]
    if missing:
        raise RecoveryPrepareError(f"source architect artifact misses required recovery files: {missing}")
    if (output_dir / "reviewer-package.json").exists() or (output_dir / "reviewer-dispatch.json").exists():
        raise RecoveryPrepareError("source already contains reviewer package/dispatch evidence")

    decision = json.loads((output_dir / "architect-decision.json").read_text(encoding="utf-8"))
    snapshot = json.loads((output_dir / "qore-state.json").read_text(encoding="utf-8"))
    contract = decision.get("review_contract")
    if (
        decision.get("status") != "REVIEW_TASK"
        or decision.get("next_actor") not in {"CLAUDE_CODE", "DEEPSEEK"}
        or not isinstance(contract, dict)
        or contract.get("enabled") is not True
    ):
        raise RecoveryPrepareError("source architect decision is not an executable external review task")
    source_main = decision.get("source_main_sha")
    if not isinstance(source_main, str) or SHA_RE.fullmatch(source_main) is None:
        raise RecoveryPrepareError("source architect decision main SHA is invalid")
    if snapshot.get("main_sha") != source_main or snapshot.get("snapshot_consistent") is not True:
        raise RecoveryPrepareError("source decision/snapshot binding failed")
    if snapshot.get("collection_errors"):
        raise RecoveryPrepareError("source QORE snapshot contains collection errors")
    return decision, snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--expected-source-head", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN is not configured")
    if not args.source_run_id.isdigit() or int(args.source_run_id) <= 0:
        raise SystemExit("source run ID is invalid")
    if SHA_RE.fullmatch(args.expected_source_head) is None:
        raise SystemExit("expected source HEAD is invalid")
    source_run_id = int(args.source_run_id)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        live = api_json(token, f"/actions/runs/{source_run_id}")
        if not isinstance(live, dict):
            raise RecoveryPrepareError("source live run payload is invalid")
        validate_source_run(live, source_run_id, args.expected_source_head)
        archive, artifact = source_artifact(token, source_run_id)
        extract_allowed(archive, output_dir)
        decision, snapshot = validate_rehydrated(output_dir)
    except RecoveryPrepareError as exc:
        raise SystemExit(f"REVIEWER_RECOVERY_PREPARE_BLOCKED: {exc}") from exc

    binding = {
        "schema_version": "qore.reviewer.dispatch.recovery.source.v1",
        "source_architect_run_id": source_run_id,
        "source_architect_head_sha": args.expected_source_head,
        "source_artifact_id": artifact.get("id"),
        "source_artifact_name": artifact.get("name"),
        "source_artifact_digest": artifact.get("digest"),
        "source_qore_main_sha": decision.get("source_main_sha"),
        "review_actor": decision.get("next_actor"),
        "review_contract_id": (decision.get("review_contract") or {}).get("contract_id"),
        "review_pr_number": (decision.get("review_contract") or {}).get("pr_number"),
        "snapshot_main_sha": snapshot.get("main_sha"),
    }
    (output_dir / "reviewer-recovery-source.json").write_text(
        json.dumps(binding, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"REVIEWER_RECOVERY_PREPARE_OK source={source_run_id} "
        f"actor={binding['review_actor']} contract={binding['review_contract_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
