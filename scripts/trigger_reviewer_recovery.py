#!/usr/bin/env python3
"""Dispatch exactly one reviewer-recovery run for a failed Autonomous V2 review task."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ORCH_REPO = "mezas3238-hue/qore-ai-orchestrator"
API = f"https://api.github.com/repos/{ORCH_REPO}"
SOURCE_WORKFLOW_NAME = "QORE Architect autonomous V2"
SOURCE_WORKFLOW_PATH = ".github/workflows/qore-architect-autonomous-v2.yml"
RECOVERY_WORKFLOW = "qore-architect-review-recovery-v1.yml"
RECOVERY_TITLE_PREFIX = "QORE Architect reviewer recovery · source R"
REQUEST_SCHEMA = "qore.reviewer.dispatch.recovery.request.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_ZIP_BYTES = 4_000_000
MAX_JSON_BYTES = 300_000
USER_AGENT = "qore-ai-orchestrator/1.0"


class RecoveryTriggerError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def api_json(token: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RecoveryTriggerError(f"GitHub API {path} failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RecoveryTriggerError(f"GitHub API {path} failed: {type(exc).__name__}") from exc


def api_status(token: str, path: str, payload: dict[str, Any]) -> int:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, headers=_headers(token), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RecoveryTriggerError(f"GitHub API {path} failed: {type(exc).__name__}") from exc


def download_artifact(token: str, artifact_id: int) -> bytes:
    request = urllib.request.Request(f"{API}/actions/artifacts/{artifact_id}/zip", headers=_headers(token))
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=45)
        data = response.read(MAX_ZIP_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise RecoveryTriggerError(f"artifact download failed with HTTP {exc.code}") from exc
        location = exc.headers.get("Location")
        if not location:
            raise RecoveryTriggerError("artifact redirect lacks Location") from exc
        try:
            with urllib.request.urlopen(location, timeout=60) as redirected:
                data = redirected.read(MAX_ZIP_BYTES + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as redirected_exc:
            raise RecoveryTriggerError(
                f"signed artifact download failed: {type(redirected_exc).__name__}"
            ) from redirected_exc
    if len(data) > MAX_ZIP_BYTES:
        raise RecoveryTriggerError("architect artifact exceeds hard ZIP size bound")
    return data


def _extract_json(archive_bytes: bytes, basename: str, *, required: bool = True) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            names = [name for name in archive.namelist() if Path(name).name == basename]
            if not names:
                if required:
                    raise RecoveryTriggerError(f"architect artifact lacks {basename}")
                return None
            if len(names) != 1:
                raise RecoveryTriggerError(f"architect artifact contains multiple {basename}")
            info = archive.getinfo(names[0])
            if info.file_size > MAX_JSON_BYTES:
                raise RecoveryTriggerError(f"{basename} exceeds hard JSON bound")
            value = json.loads(archive.read(info).decode("utf-8"))
    except RecoveryTriggerError:
        raise
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise RecoveryTriggerError(f"could not decode {basename}") from exc
    if not isinstance(value, dict):
        raise RecoveryTriggerError(f"{basename} is not an object")
    return value


def validate_source_run(run: dict[str, Any], run_id: int, expected_head: str) -> None:
    if run.get("id") != run_id:
        raise RecoveryTriggerError("source architect run ID mismatch")
    if run.get("name") != SOURCE_WORKFLOW_NAME or run.get("path") != SOURCE_WORKFLOW_PATH:
        raise RecoveryTriggerError("source run is not the canonical Autonomous V2 workflow")
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main":
        raise RecoveryTriggerError("source architect run origin is not trusted")
    if run.get("head_sha") != expected_head:
        raise RecoveryTriggerError("source architect run HEAD mismatch")
    if run.get("status") != "completed" or run.get("conclusion") != "failure":
        raise RecoveryTriggerError("recovery requires a completed failed Autonomous V2 run")


def recovery_needed(archive_bytes: bytes) -> bool:
    decision = _extract_json(archive_bytes, "architect-decision.json")
    assert decision is not None
    contract = decision.get("review_contract")
    if (
        decision.get("status") != "REVIEW_TASK"
        or decision.get("next_actor") not in {"CLAUDE_CODE", "DEEPSEEK"}
        or not isinstance(contract, dict)
        or contract.get("enabled") is not True
    ):
        return False
    if _extract_json(archive_bytes, "reviewer-package.json", required=False) is not None:
        return False
    if _extract_json(archive_bytes, "reviewer-dispatch.json", required=False) is not None:
        return False
    source_main = decision.get("source_main_sha")
    if not isinstance(source_main, str) or SHA_RE.fullmatch(source_main) is None:
        raise RecoveryTriggerError("review task source_main_sha is invalid")
    return True


def _source_artifact(token: str, run_id: int) -> bytes:
    payload = api_json(token, f"/actions/runs/{run_id}/artifacts?per_page=100")
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise RecoveryTriggerError("source architect artifact list is invalid")
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
        raise RecoveryTriggerError(f"expected one non-expired {name}; found {len(matches)}")
    return download_artifact(token, matches[0]["id"])


def _existing_recovery(token: str, source_run_id: int) -> dict[str, Any] | None:
    payload = api_json(
        token,
        f"/actions/workflows/{RECOVERY_WORKFLOW}/runs?event=workflow_dispatch&per_page=50",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise RecoveryTriggerError("recovery workflow history is invalid")
    title = f"{RECOVERY_TITLE_PREFIX}{source_run_id}"
    matches = [
        run
        for run in payload["workflow_runs"]
        if isinstance(run, dict) and run.get("display_title") == title
    ]
    if len(matches) > 1:
        raise RecoveryTriggerError("multiple recovery runs exist for one source architect run")
    return matches[0] if matches else None


def dispatch_recovery(token: str, source_run_id: int, expected_head: str) -> int | None:
    existing = _existing_recovery(token, source_run_id)
    if existing is not None:
        run_id = existing.get("id")
        if type(run_id) is not int:
            raise RecoveryTriggerError("existing recovery run ID is invalid")
        return None

    before_payload = api_json(
        token,
        f"/actions/workflows/{RECOVERY_WORKFLOW}/runs?event=workflow_dispatch&per_page=50",
    )
    if not isinstance(before_payload, dict) or not isinstance(before_payload.get("workflow_runs"), list):
        raise RecoveryTriggerError("could not snapshot recovery history before dispatch")
    before_ids = {
        run.get("id")
        for run in before_payload["workflow_runs"]
        if isinstance(run, dict) and type(run.get("id")) is int
    }
    status = api_status(
        token,
        f"/actions/workflows/{RECOVERY_WORKFLOW}/dispatches",
        {
            "ref": "main",
            "inputs": {
                "source_architect_run_id": str(source_run_id),
                "expected_source_head_sha": expected_head,
            },
        },
    )
    if status != 204:
        raise RecoveryTriggerError(f"reviewer recovery dispatch failed with HTTP {status}")

    title = f"{RECOVERY_TITLE_PREFIX}{source_run_id}"
    for _attempt in range(20):
        time.sleep(2)
        payload = api_json(
            token,
            f"/actions/workflows/{RECOVERY_WORKFLOW}/runs?event=workflow_dispatch&per_page=50",
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            continue
        matches = [
            run
            for run in payload["workflow_runs"]
            if isinstance(run, dict)
            and type(run.get("id")) is int
            and run.get("id") not in before_ids
            and run.get("display_title") == title
            and run.get("head_branch") == "main"
        ]
        if len(matches) == 1:
            return matches[0]["id"]
        if len(matches) > 1:
            raise RecoveryTriggerError("recovery dispatch created multiple matching runs")
    raise RecoveryTriggerError("recovery dispatch returned 204 but no exact run was observed")


def source_from_event(event: dict[str, Any], request_path: Path) -> tuple[int, str]:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    if event_name == "workflow_run":
        run = event.get("workflow_run")
        if not isinstance(run, dict):
            raise RecoveryTriggerError("workflow_run event lacks source run")
        run_id = run.get("id")
        head = run.get("head_sha")
        if type(run_id) is not int or not isinstance(head, str) or SHA_RE.fullmatch(head) is None:
            raise RecoveryTriggerError("workflow_run source identity is invalid")
        return run_id, head
    if event_name == "push":
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or request.get("schema_version") != REQUEST_SCHEMA:
            raise RecoveryTriggerError("recovery request schema is invalid")
        run_id = request.get("source_architect_run_id")
        head = request.get("expected_source_head_sha")
        if type(run_id) is not int or run_id <= 0:
            raise RecoveryTriggerError("recovery request source run ID is invalid")
        if not isinstance(head, str) or SHA_RE.fullmatch(head) is None:
            raise RecoveryTriggerError("recovery request source HEAD is invalid")
        return run_id, head
    raise RecoveryTriggerError(f"unsupported recovery trigger event {event_name!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN is not configured")
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise SystemExit("GitHub event payload is not an object")

    try:
        source_run_id, expected_head = source_from_event(event, Path(args.request))
        live = api_json(token, f"/actions/runs/{source_run_id}")
        if not isinstance(live, dict):
            raise RecoveryTriggerError("source architect live run is invalid")
        validate_source_run(live, source_run_id, expected_head)
        archive = _source_artifact(token, source_run_id)
        needed = recovery_needed(archive)
        recovery_run_id = dispatch_recovery(token, source_run_id, expected_head) if needed else None
    except RecoveryTriggerError as exc:
        raise SystemExit(f"REVIEWER_RECOVERY_TRIGGER_BLOCKED: {exc}") from exc

    result = {
        "schema_version": "qore.reviewer.dispatch.recovery.trigger.v1",
        "source_architect_run_id": source_run_id,
        "expected_source_head_sha": expected_head,
        "recovery_needed": needed,
        "recovery_run_id": recovery_run_id,
        "dispatched": recovery_run_id is not None,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"REVIEWER_RECOVERY_TRIGGER_OK source={source_run_id} needed={needed} "
        f"dispatched={recovery_run_id is not None} recovery={recovery_run_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
