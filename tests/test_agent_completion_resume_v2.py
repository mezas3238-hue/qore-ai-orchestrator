from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import resume_after_agent_completion as base  # noqa: E402
import resume_after_agent_completion_v2 as bridge  # noqa: E402


class ReviewerResumeV2Tests(unittest.TestCase):
    def _request_payload(self, package: str, head: str) -> dict[str, str]:
        raw = json.dumps({"package_id": package, "expected_head": head}).encode("utf-8")
        return {"content": base64.b64encode(raw).decode("ascii")}

    def test_custom_run_name_is_valid_when_workflow_path_is_exact(self) -> None:
        package = "QORE-SOL-012345abcdef-DS-EXPERT-R123"
        candidate = "1" * 40
        reviewer_head = "2" * 40
        run = {
            "id": 456,
            "name": f"DeepSeek QORE review · {package}",
            "display_title": f"DeepSeek QORE review · {package}",
            "path": ".github/workflows/deepseek-qore-review.yml",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": reviewer_head,
            "run_attempt": 1,
        }
        event = {
            "client_payload": {
                "schema_version": "qore.agent.completion.v1",
                "repository": "mezas3238-hue/qore-deepseek-reviewer",
                "actor": "DEEPSEEK",
                "workflow_run_id": 456,
                "workflow_run_attempt": 1,
                "package_id": package,
            }
        }

        def fake_api(_token: str, _api: str, path: str, **_kwargs: object) -> object:
            if path == "/actions/runs/456":
                return run
            if path.startswith("/contents/requests/current.json"):
                return self._request_payload(package, candidate)
            raise AssertionError(path)

        with patch.object(base, "api_json", side_effect=fake_api):
            completion = bridge.parse_reviewer_event(event, "reviewer-token")

        self.assertEqual(completion["package_id"], package)
        self.assertEqual(completion["package_parent_architect_run_id"], 123)
        self.assertEqual(completion["source_main_sha"], candidate)

    def test_wrong_reviewer_workflow_path_fails_closed(self) -> None:
        package = "QORE-SOL-012345abcdef-DS-EXPERT-R123"
        run = {
            "id": 456,
            "name": f"DeepSeek QORE review · {package}",
            "display_title": f"DeepSeek QORE review · {package}",
            "path": ".github/workflows/other.yml",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": "2" * 40,
            "run_attempt": 1,
        }
        event = {
            "client_payload": {
                "schema_version": "qore.agent.completion.v1",
                "repository": "mezas3238-hue/qore-deepseek-reviewer",
                "actor": "DEEPSEEK",
                "workflow_run_id": 456,
                "workflow_run_attempt": 1,
                "package_id": package,
            }
        }
        with patch.object(base, "api_json", return_value=run):
            with self.assertRaisesRegex(base.ResumeError, "origin is not trusted"):
                bridge.parse_reviewer_event(event, "reviewer-token")

    def test_successful_recovery_parent_anchors_to_original_architect(self) -> None:
        recovery_id = 22
        source_id = 11
        source_head = "3" * 40
        digest = "sha256:" + "4" * 64
        recovery_run = {
            "id": recovery_id,
            "name": "QORE Architect reviewer recovery · source R11",
            "path": ".github/workflows/qore-architect-review-recovery-v1.yml",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": "5" * 40,
        }
        source_run = {
            "id": source_id,
            "name": "QORE Architect autonomous V2",
            "path": ".github/workflows/qore-architect-autonomous-v2.yml",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
            "head_sha": source_head,
        }
        source_evidence = {
            "schema_version": "qore.reviewer.dispatch.recovery.source.v1",
            "source_architect_run_id": source_id,
            "source_architect_head_sha": source_head,
            "source_artifact_id": 77,
            "source_artifact_name": f"qore-architect-v2-{source_id}",
            "source_artifact_digest": digest,
        }

        def fake_api(_token: str, _api: str, path: str, **_kwargs: object) -> object:
            if path == f"/actions/runs/{recovery_id}":
                return recovery_run
            if path == f"/actions/runs/{source_id}":
                return source_run
            raise AssertionError(path)

        def fake_artifact(_token: str, _repo: str, run_id: int, _name: str) -> bytes:
            return b"recovery" if run_id == recovery_id else b"source"

        with (
            patch.object(base, "api_json", side_effect=fake_api),
            patch.object(base, "artifact_bytes", side_effect=fake_artifact),
            patch.object(base, "extract_json", return_value=source_evidence),
            patch.object(bridge, "_exact_artifact_metadata", return_value={"id": 77, "digest": digest}),
        ):
            canonical, package_archive, cost_archive = bridge.resolve_reviewer_parent(
                "token", recovery_id
            )

        self.assertEqual(canonical, source_id)
        self.assertEqual(package_archive, b"recovery")
        self.assertEqual(cost_archive, b"source")

    def test_recovery_source_artifact_digest_mismatch_fails_closed(self) -> None:
        recovery_id = 22
        source_id = 11
        source_head = "3" * 40
        digest = "sha256:" + "4" * 64
        runs = {
            recovery_id: {
                "id": recovery_id,
                "path": ".github/workflows/qore-architect-review-recovery-v1.yml",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "head_branch": "main",
            },
            source_id: {
                "id": source_id,
                "name": "QORE Architect autonomous V2",
                "path": ".github/workflows/qore-architect-autonomous-v2.yml",
                "event": "workflow_dispatch",
                "status": "completed",
                "head_branch": "main",
                "head_sha": source_head,
            },
        }
        evidence = {
            "schema_version": "qore.reviewer.dispatch.recovery.source.v1",
            "source_architect_run_id": source_id,
            "source_architect_head_sha": source_head,
            "source_artifact_id": 77,
            "source_artifact_name": f"qore-architect-v2-{source_id}",
            "source_artifact_digest": digest,
        }

        def fake_api(_token: str, _api: str, path: str, **_kwargs: object) -> object:
            run_id = int(path.rsplit("/", 1)[1])
            return runs[run_id]

        with (
            patch.object(base, "api_json", side_effect=fake_api),
            patch.object(base, "artifact_bytes", return_value=b"archive"),
            patch.object(base, "extract_json", return_value=evidence),
            patch.object(
                bridge,
                "_exact_artifact_metadata",
                return_value={"id": 77, "digest": "sha256:" + "9" * 64},
            ),
        ):
            with self.assertRaisesRegex(base.ResumeError, "identity/digest mismatch"):
                bridge.resolve_reviewer_parent("token", recovery_id)


if __name__ == "__main__":
    unittest.main()
