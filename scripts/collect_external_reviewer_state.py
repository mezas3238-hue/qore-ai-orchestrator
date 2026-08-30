#!/usr/bin/env python3
"""Collect bounded state from the existing private Claude/DeepSeek reviewer repositories."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

CLAUDE_REPO = "mezas3238-hue/qore-claude-reviewer"
DEEPSEEK_REPO = "mezas3238-hue/qore-deepseek-reviewer"
USER_AGENT = "qore-ai-orchestrator/1.0"
MAX_ZIP_BYTES = 5_000_000
MAX_REVIEW_CHARS = 64_000


class ReviewerStateError(RuntimeError):
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


def api_json(repo: str, path: str, token: str, params: dict[str, str] | None = None) -> Any:
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    url = f"https://api.github.com/repos/{repo}{path}{query}"
    request = urllib.request.Request(url, headers=headers(token))
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ReviewerStateError(f"{repo}{path}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ReviewerStateError(f"{repo}{path}: {type(exc).__name__}") from exc


def decode_content(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
        raise ReviewerStateError(f"{label}: contents payload is invalid")
    try:
        decoded = base64.b64decode(payload["content"]).decode("utf-8")
        value = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewerStateError(f"{label}: requests/current.json is invalid") from exc
    if not isinstance(value, dict):
        raise ReviewerStateError(f"{label}: requests/current.json is not an object")
    return value


def safe_current_request(repo: str, token: str) -> dict[str, Any]:
    payload = api_json(repo, "/contents/requests/current.json", token, {"ref": "main"})
    value = decode_content(payload, repo)
    keep = {
        "package_id",
        "pr_number",
        "expected_base",
        "expected_head",
        "expected_synthetic",
        "review_mode",
        "prompt_path",
        "dispatch_nonce",
    }
    current = {key: value.get(key) for key in keep if key in value}
    qg = value.get("qg")
    if isinstance(qg, dict):
        current["qg"] = {
            "run_id": qg.get("run_id"),
            "job_id": qg.get("job_id"),
            "expected": qg.get("expected"),
        }
    qg_summary = value.get("qg_summary")
    if isinstance(qg_summary, dict):
        current["qg_summary"] = qg_summary
    return current


def latest_named_artifact(repo: str, name: str, token: str) -> dict[str, Any] | None:
    payload = api_json(repo, "/actions/artifacts", token, {"name": name, "per_page": "100"})
    if not isinstance(payload, dict):
        raise ReviewerStateError(f"{repo}: artifact list is invalid")
    artifacts = [
        item
        for item in payload.get("artifacts", [])
        if isinstance(item, dict) and item.get("name") == name and item.get("expired") is False
    ]
    if not artifacts:
        return None
    artifacts.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return artifacts[0]


def download_artifact_zip(repo: str, artifact_id: int, token: str) -> bytes:
    url = f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(url, headers=headers(token))
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=45)
        data = response.read(MAX_ZIP_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise ReviewerStateError(f"{repo}: artifact download HTTP {exc.code}") from exc
        location = exc.headers.get("Location")
        if not location:
            raise ReviewerStateError(f"{repo}: artifact redirect lacks Location") from exc
        # The redirect is a short-lived signed URL. Never forward the GitHub token to it.
        try:
            with urllib.request.urlopen(location, timeout=60) as redirected:
                data = redirected.read(MAX_ZIP_BYTES + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as redirected_exc:
            raise ReviewerStateError(
                f"{repo}: signed artifact download failed: {type(redirected_exc).__name__}"
            ) from redirected_exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ReviewerStateError(f"{repo}: artifact download failed: {type(exc).__name__}") from exc
    if len(data) > MAX_ZIP_BYTES:
        raise ReviewerStateError(f"{repo}: artifact ZIP exceeds bounded size")
    return data


def extract_text_file(zip_bytes: bytes, filename: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = [name for name in archive.namelist() if Path(name).name == filename]
            if len(names) != 1:
                raise ReviewerStateError(
                    f"artifact must contain exactly one {filename}; found {len(names)}"
                )
            info = archive.getinfo(names[0])
            if info.file_size > MAX_REVIEW_CHARS * 4:
                raise ReviewerStateError(f"{filename} exceeds bounded uncompressed size")
            text = archive.read(info).decode("utf-8")
    except (zipfile.BadZipFile, UnicodeError, KeyError) as exc:
        raise ReviewerStateError(f"artifact {filename} could not be decoded") from exc
    return text[:MAX_REVIEW_CHARS]


def classify_claude_review(text: str) -> str:
    clean = "HALLAZGOS: NINGUNO" in text and "VALIDACIÓN OK" in text
    not_ok = "VALIDACIÓN NO OK" in text
    mechanical = "MECHANICAL REVIEW FAILURE" in text
    if clean and not not_ok and not mechanical:
        return "CLEAN"
    if not_ok and not clean and not mechanical:
        return "FINDINGS"
    if mechanical and not clean and not not_ok:
        return "MECHANICAL_FAILURE"
    return "AMBIGUOUS"


def collect_claude(token: str) -> dict[str, Any]:
    current = safe_current_request(CLAUDE_REPO, token)
    package_id = current.get("package_id")
    state: dict[str, Any] = {
        "repository": CLAUDE_REPO,
        "current_request": current,
        "status": "NO_PACKAGE" if not isinstance(package_id, str) or not package_id else "PENDING_OR_UNKNOWN",
        "artifact": None,
        "review": None,
    }
    if not isinstance(package_id, str) or not package_id:
        return state

    artifact_name = f"claude-{package_id}"
    artifact = latest_named_artifact(CLAUDE_REPO, artifact_name, token)
    if artifact is None:
        return state
    artifact_id = artifact.get("id")
    if type(artifact_id) is not int or artifact_id <= 0:
        raise ReviewerStateError("Claude artifact ID is invalid")
    zip_bytes = download_artifact_zip(CLAUDE_REPO, artifact_id, token)
    review_text = extract_text_file(zip_bytes, "claude-review.md")
    state["status"] = "COMPLETED"
    state["artifact"] = {
        "id": artifact_id,
        "name": artifact.get("name"),
        "created_at": artifact.get("created_at"),
        "expires_at": artifact.get("expires_at"),
        "digest": artifact.get("digest"),
    }
    state["review"] = {
        "verdict": classify_claude_review(review_text),
        "text": review_text,
    }
    return state


def collect_deepseek(token: str) -> dict[str, Any]:
    current = safe_current_request(DEEPSEEK_REPO, token)
    package_id = current.get("package_id")
    return {
        "repository": DEEPSEEK_REPO,
        "current_request": current,
        "status": "NO_PACKAGE" if not isinstance(package_id, str) or not package_id else "REQUEST_PRESENT",
        "result_source": "qore-core pull-request reviews",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    token = os.environ.get("QORE_REVIEWER_DISPATCH_TOKEN", "").strip()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not token:
        payload = {
            "schema_version": "qore.external.reviewers.v1",
            "configured": False,
            "errors": [],
            "claude": None,
            "deepseek": None,
        }
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("EXTERNAL_REVIEWER_STATE configured=false")
        return 0

    errors: list[str] = []
    claude: dict[str, Any] | None = None
    deepseek: dict[str, Any] | None = None
    try:
        claude = collect_claude(token)
    except ReviewerStateError as exc:
        errors.append(f"claude:{exc}")
    try:
        deepseek = collect_deepseek(token)
    except ReviewerStateError as exc:
        errors.append(f"deepseek:{exc}")

    payload = {
        "schema_version": "qore.external.reviewers.v1",
        "configured": True,
        "errors": errors,
        "claude": claude,
        "deepseek": deepseek,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "EXTERNAL_REVIEWER_STATE configured=true errors={} claude={} deepseek={}".format(
            len(errors),
            None if claude is None else claude.get("status"),
            None if deepseek is None else deepseek.get("status"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
