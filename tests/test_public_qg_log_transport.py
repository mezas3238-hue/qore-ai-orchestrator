from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_reviewer_package as package  # noqa: E402
import build_reviewer_package_public_logs as public_logs  # noqa: E402


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        return False

    def read(self) -> bytes:
        return self.payload


class PublicQGLogTransportTests(unittest.TestCase):
    def test_public_headers_never_carry_authorization(self) -> None:
        headers = public_logs.public_headers()
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Accept"], "application/vnd.github+json")

    def test_public_attestation_requires_exact_public_qore_repo(self) -> None:
        payload = {
            "full_name": public_logs.QORE_REPO,
            "private": False,
            "visibility": "public",
        }
        with patch.object(
            public_logs.urllib.request,
            "urlopen",
            return_value=FakeResponse(json.dumps(payload).encode()),
        ):
            observed = public_logs.attest_public_qore_repo()
        self.assertEqual(observed["full_name"], public_logs.QORE_REPO)

        private_payload = dict(payload, private=True, visibility="private")
        with patch.object(
            public_logs.urllib.request,
            "urlopen",
            return_value=FakeResponse(json.dumps(private_payload).encode()),
        ):
            with self.assertRaisesRegex(public_logs.PublicLogTransportError, "not attested public"):
                public_logs.attest_public_qore_repo()

    def test_public_log_download_is_exact_endpoint_and_unauthenticated(self) -> None:
        captured = []

        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            captured.append(request)
            return FakeResponse(b"qg-log")

        with patch.object(public_logs.urllib.request, "urlopen", side_effect=fake_urlopen):
            text = public_logs.public_api_text("/actions/jobs/99181893347/logs")
        self.assertEqual(text, "qg-log")
        self.assertEqual(len(captured), 1)
        self.assertIsNone(captured[0].get_header("Authorization"))

        with self.assertRaisesRegex(package.PackageError, "restricted to exact job-log"):
            public_logs.public_api_text("/actions/runs/123/jobs")


if __name__ == "__main__":
    unittest.main()
