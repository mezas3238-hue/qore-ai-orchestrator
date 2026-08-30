#!/usr/bin/env python3
"""Dispatch one deterministic Codex worker package with anti-duplication."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = "mezas3238-hue/qore-ai-orchestrator"
API = f"https://api.github.com/repos/{REPO}"
WORKFLOW = "codex-engineer-worker.yml"
PACKAGE_RE = re.compile(r"^QORE-CODEX-[0-9a-f]{12}-[0-9a-f]{16}$")
USER_AGENT = "qore-ai-orchestrator/1.0"


class DispatchError(RuntimeError):
    pass


def headers(token: str) -> dict[str, str]:
    return {"Accept":"application/vnd.github+json","Authorization":f"Bearer {token}","Content-Type":"application/json","User-Agent":USER_AGENT,"X-GitHub-Api-Version":"2022-11-28"}


def api(token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(API + path, data=data, headers=headers(token), method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try: body: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError: body = None
        return exc.code, body


def runs(token: str) -> list[dict[str, Any]]:
    status, payload = api(token, "GET", f"/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=50")
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise DispatchError(f"Codex workflow run lookup failed with HTTP {status}")
    return [run for run in payload["workflow_runs"] if isinstance(run, dict)]


def package_for_run(run: dict[str, Any]) -> str | None:
    title = str(run.get("display_title") or "")
    prefix = "Codex worker · "
    candidate = title[len(prefix):].strip() if title.startswith(prefix) else ""
    return candidate if PACKAGE_RE.fullmatch(candidate) else None


def existing_package_run(token: str, package_id: str) -> dict[str, Any] | None:
    for run in runs(token):
        if package_for_run(run) == package_id:
            return run
    return None


def summary(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(run, dict): return None
    return {"run_id":run.get("id"),"package_id":package_for_run(run),"status":run.get("status"),"conclusion":run.get("conclusion"),"created_at":run.get("created_at"),"updated_at":run.get("updated_at"),"head_sha":run.get("head_sha")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--mode", choices=["dry_run", "execute"], required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token: raise DispatchError("GITHUB_TOKEN is required")
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if not isinstance(request, dict) or request.get("schema_version") != "qore.codex.engineering.request.v1":
        raise DispatchError("invalid Codex request")
    package_id = request.get("package_id")
    if not isinstance(package_id, str) or not PACKAGE_RE.fullmatch(package_id):
        raise DispatchError("invalid package_id")
    if request.get("production_authority") is not False:
        raise DispatchError("Production authority is forbidden")

    existing = existing_package_run(token, package_id)
    if existing is not None:
        result = {"schema_version":"qore.codex.dispatch.v1","package_id":package_id,"mode":args.mode,"dispatched":False,"reason":"EXACT_PACKAGE_ALREADY_HAS_RUN","run":summary(existing),"production_authority":False}
    elif args.mode == "dry_run":
        result = {"schema_version":"qore.codex.dispatch.v1","package_id":package_id,"mode":"dry_run","dispatched":False,"reason":"DRY_RUN_ONLY","run":None,"production_authority":False}
    else:
        compact_request = json.dumps(request, separators=(",", ":"), ensure_ascii=False)
        status, _ = api(token, "POST", f"/actions/workflows/{WORKFLOW}/dispatches", {
            "ref":"main",
            "inputs":{
                "package_id":package_id,
                "confirm_api_spend":"true",
                "publish_candidate":"true",
                "request_json":compact_request,
            },
        })
        if status != 204: raise DispatchError(f"Codex workflow dispatch failed with HTTP {status}")
        observed: dict[str, Any] | None = None
        for _attempt in range(15):
            time.sleep(2)
            observed = existing_package_run(token, package_id)
            if observed is not None: break
        if observed is None: raise DispatchError("workflow_dispatch returned 204 but package run was not observed")
        result = {"schema_version":"qore.codex.dispatch.v1","package_id":package_id,"mode":"execute","dispatched":True,"reason":"DISPATCHED","run":summary(observed),"production_authority":False}

    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CODEX_DISPATCH_OK package={package_id} mode={args.mode} dispatched={result['dispatched']} reason={result['reason']}")
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except DispatchError as exc:
        print(f"CODEX_DISPATCH_ERROR: {exc}")
        raise SystemExit(10) from exc
