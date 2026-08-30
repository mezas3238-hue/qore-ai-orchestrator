#!/usr/bin/env python3
"""Collect bounded Codex worker run/result state and merge it into the Sol snapshot."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO = "mezas3238-hue/qore-ai-orchestrator"
API = f"https://api.github.com/repos/{REPO}"
WORKFLOW = "codex-engineer-worker.yml"
USER_AGENT = "qore-ai-orchestrator/1.0"
PACKAGE_RE = re.compile(r"QORE-CODEX-[0-9a-f]{12}-[0-9a-f]{16}")
MAX_RUNS = 20
MAX_ZIP_BYTES = 3_000_000
MAX_JSON_BYTES = 200_000


class CodexStateError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def api_json(token: str, path: str) -> Any:
    request = urllib.request.Request(API + path, headers=headers(token))
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CodexStateError(f"{path}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CodexStateError(f"{path}: {type(exc).__name__}") from exc


def run_summary(run: dict[str, Any]) -> dict[str, Any]:
    title = str(run.get("display_title") or "")
    match = PACKAGE_RE.search(title)
    return {
        "run_id": run.get("id"),
        "package_id": match.group(0) if match else None,
        "display_title": title,
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "event": run.get("event"),
        "head_sha": run.get("head_sha"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
    }


def download_artifact(token: str, artifact_id: int) -> bytes:
    request = urllib.request.Request(f"{API}/actions/artifacts/{artifact_id}/zip", headers=headers(token))
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=45)
        data = response.read(MAX_ZIP_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise CodexStateError(f"artifact download HTTP {exc.code}") from exc
        location = exc.headers.get("Location")
        if not location:
            raise CodexStateError("artifact redirect has no Location") from exc
        try:
            with urllib.request.urlopen(location, timeout=60) as redirected:
                data = redirected.read(MAX_ZIP_BYTES + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as redirected_exc:
            raise CodexStateError(f"signed artifact download failed: {type(redirected_exc).__name__}") from redirected_exc
    if len(data) > MAX_ZIP_BYTES:
        raise CodexStateError("Codex artifact exceeds hard ZIP size bound")
    return data


def extract_json(archive_bytes: bytes, basename: str) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            names = [name for name in archive.namelist() if Path(name).name == basename]
            if not names:
                return None
            if len(names) != 1:
                raise CodexStateError(f"artifact contains multiple {basename}")
            info = archive.getinfo(names[0])
            if info.file_size > MAX_JSON_BYTES:
                raise CodexStateError(f"{basename} exceeds hard size bound")
            value = json.loads(archive.read(info).decode("utf-8"))
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise CodexStateError(f"could not decode {basename}") from exc
    if not isinstance(value, dict):
        raise CodexStateError(f"{basename} is not an object")
    return value


def result_for_run(token: str, run_id: int) -> dict[str, Any] | None:
    payload = api_json(token, f"/actions/runs/{run_id}/artifacts?per_page=100")
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise CodexStateError("artifact list is invalid")
    target_name = f"qore-codex-worker-{run_id}"
    matches = [
        item for item in payload["artifacts"]
        if isinstance(item, dict) and item.get("name") == target_name and item.get("expired") is False
    ]
    if not matches:
        return None
    if len(matches) != 1 or type(matches[0].get("id")) is not int:
        raise CodexStateError("Codex worker artifact identity is ambiguous")
    data = download_artifact(token, matches[0]["id"])
    return {
        "worker_result": extract_json(data, "codex-worker-result.json"),
        "usage": extract_json(data, "codex-worker-usage.json"),
        "controller_qg": extract_json(data, "codex-controller-qg.json"),
        "publication": extract_json(data, "codex-publication.json"),
    }


def collect(token: str) -> dict[str, Any]:
    payload = api_json(token, f"/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page={MAX_RUNS}")
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise CodexStateError("Codex workflow run list is invalid")
    summaries = [run_summary(run) for run in payload["workflow_runs"] if isinstance(run, dict)]
    active = [run for run in summaries if run.get("status") in {"queued", "in_progress"} and run.get("package_id")]
    latest_completed: dict[str, Any] | None = None
    for run in summaries:
        if run.get("status") != "completed" or not run.get("package_id"):
            continue
        run_id = run.get("run_id")
        details = result_for_run(token, run_id) if type(run_id) is int else None
        latest_completed = {**run, "evidence": details}
        break
    return {
        "schema_version": "qore.codex.worker.state.v1",
        "repository": REPO,
        "workflow": WORKFLOW,
        "active_runs": active,
        "latest_completed": latest_completed,
        "recent_runs": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--state-output", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN is required for Codex worker state collection")
    state = collect(token)
    state_path = Path(args.state_output)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    snapshot_path = Path(args.snapshot)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict):
        raise SystemExit("QORE snapshot must be an object")
    snapshot["codex_worker_state"] = state
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CODEX_WORKER_STATE active={len(state['active_runs'])} completed={state['latest_completed'] is not None}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CodexStateError as exc:
        print(f"CODEX_WORKER_STATE_ERROR: {exc}")
        raise SystemExit(9) from exc
