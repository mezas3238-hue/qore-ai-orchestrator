#!/usr/bin/env python3
"""Compatibility entrypoint for reviewer-package QG evidence.

The public-log transport helpers remain only for regression coverage of the
failed transport experiment. Operational package construction no longer calls
the GitHub job-log endpoint. ``main`` delegates to the exact frozen-QG builder,
which reuses a summary already bound into the immutable architect snapshot and
revalidates the referenced QORE CI run/job live.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import build_reviewer_package as package
import build_reviewer_package_frozen_qg as frozen_qg

QORE_REPO = "mezas3238-hue/qore-core"
QORE_REPO_API = f"https://api.github.com/repos/{QORE_REPO}"
USER_AGENT = "qore-ai-orchestrator/1.0"


class PublicLogTransportError(RuntimeError):
    pass


def public_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def attest_public_qore_repo() -> dict[str, Any]:
    request = urllib.request.Request(QORE_REPO_API, headers=public_headers())
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PublicLogTransportError(
            f"public qore-core attestation failed with HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicLogTransportError(
            f"public qore-core attestation failed: {type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict):
        raise PublicLogTransportError("public qore-core attestation is not an object")
    if payload.get("full_name") != QORE_REPO:
        raise PublicLogTransportError("public qore-core attestation repository mismatch")
    if payload.get("private") is not False or payload.get("visibility") != "public":
        raise PublicLogTransportError("qore-core is not attested public; refusing unauthenticated log transport")
    return payload


def public_api_text(path: str) -> str:
    """Legacy diagnostic helper. It is intentionally not used by main()."""
    if not path.startswith("/actions/jobs/") or not path.endswith("/logs"):
        raise package.PackageError("public transport is restricted to exact job-log endpoints")
    request = urllib.request.Request(package.API + path, headers=public_headers())
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8-sig", errors="strict")
    except urllib.error.HTTPError as exc:
        raise package.PackageError(f"public GitHub log API {path} failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, UnicodeError) as exc:
        raise package.PackageError(
            f"public GitHub log API {path} failed: {type(exc).__name__}"
        ) from exc


def main() -> int:
    return frozen_qg.main()


if __name__ == "__main__":
    raise SystemExit(main())
