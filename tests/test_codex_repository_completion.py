from __future__ import annotations

import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import resume_after_agent_completion as base  # noqa: E402
import resume_after_agent_completion_v2 as subject  # noqa: E402


class CodexRepositoryCompletionTests(unittest.TestCase):
    package = "QORE-CODEX-5a158ef0fb2e-490b66f50c14c5a4"
    run_id = 33335805409
    architect_run_id = 33335734078
    source_sha = "5a158ef0fb2e21db95f2be0685373780bf1ab197"
    orchestrator_sha = "a" * 40

    def _event(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "qore.agent.completion.v1",
            "repository": base.ORCH_REPO,
            "actor": "CODEX",
            "workflow_run_id": self.run_id,
            "workflow_run_attempt": 1,
            "package_id": self.package,
        }
        payload.update(overrides)
        return {"client_payload": payload}

    def _run(self, **overrides: object) -> dict[str, object]:
        run: dict[str, object] = {
            "id": self.run_id,
            "run_attempt": 1,
            "path": ".github/workflows/codex-engineer-worker.yml",
            "event": "workflow_dispatch",
            "status": "in_progress",
            "conclusion": None,
            "head_branch": "main",
            "head_sha": self.orchestrator_sha,
            "display_title": f"Codex worker · {self.package}",
        }
        run.update(overrides)
        return run

    def _jobs(self, **worker_overrides: object) -> dict[str, object]:
        worker: dict[str, object] = {
            "name": "worker",
            "status": "completed",
            "conclusion": "success",
        }
        worker.update(worker_overrides)
        return {
            "jobs": [
                worker,
                {"name": "completion-callback", "status": "in_progress", "conclusion": None},
            ]
        }

    def _archive(self, *, request_package: str | None = None) -> bytes:
        request = {
            "schema_version": "qore.codex.engineering.request.v1",
            "package_id": request_package or self.package,
            "architect_run_id": str(self.architect_run_id),
            "source_main_sha": self.source_sha,
        }
        result = {
            "schema_version": "qore.codex.worker.result.v1",
            "source_main_sha": self.source_sha,
            "status": "BLOCKED",
            "production_authority": False,
        }
        usage = {
            "model": "gpt-5.3-codex",
            "input_tokens": 1000,
            "cached_tokens": 800,
            "cache_write_tokens": 0,
            "output_tokens": 100,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("codex-request.json", json.dumps(request))
            bundle.writestr("codex-worker-result.json", json.dumps(result))
            bundle.writestr("codex-worker-usage.json", json.dumps(usage))
        return buffer.getvalue()

    def _parse(
        self,
        *,
        event: dict[str, object] | None = None,
        run: dict[str, object] | None = None,
        jobs: dict[str, object] | None = None,
        archive: bytes | None = None,
    ) -> dict[str, object]:
        live = run or self._run()
        worker_jobs = jobs or self._jobs()

        def fake_api(_token: str, _base: str, path: str, **_kwargs: object) -> object:
            if path == f"/actions/runs/{self.run_id}":
                return live
            if path == f"/actions/runs/{self.run_id}/jobs?filter=latest&per_page=100":
                return worker_jobs
            raise AssertionError(path)

        with patch.object(base, "api_json", side_effect=fake_api), patch.object(
            base, "artifact_bytes", return_value=archive or self._archive()
        ):
            return subject.parse_codex_repository_event(event or self._event(), "token")

    def test_accepts_callback_while_callback_job_keeps_run_in_progress(self) -> None:
        completion = self._parse()
        self.assertEqual(completion["actor"], "CODEX")
        self.assertEqual(completion["repo"], base.ORCH_REPO)
        self.assertEqual(completion["run_id"], self.run_id)
        self.assertEqual(completion["package_id"], self.package)
        self.assertEqual(completion["parent_architect_run_id"], self.architect_run_id)
        self.assertEqual(completion["source_main_sha"], self.source_sha)
        self.assertEqual(completion["agent_cost_kind"], "observed")

    def test_accepts_already_completed_successful_source_run(self) -> None:
        completion = self._parse(run=self._run(status="completed", conclusion="success"))
        self.assertEqual(completion["conclusion"], "success")

    def test_rejects_wrong_actor_or_repository(self) -> None:
        with self.assertRaisesRegex(base.ResumeError, "actor/repository"):
            self._parse(event=self._event(actor="DEEPSEEK"))
        with self.assertRaisesRegex(base.ResumeError, "actor/repository"):
            self._parse(event=self._event(repository="mezas3238-hue/qore-core"))

    def test_rejects_wrong_workflow_path_even_with_valid_title(self) -> None:
        with self.assertRaisesRegex(base.ResumeError, "origin is not trusted"):
            self._parse(run=self._run(path=".github/workflows/other.yml"))

    def test_rejects_incomplete_worker_job(self) -> None:
        with self.assertRaisesRegex(base.ResumeError, "worker job is not exact completed success"):
            self._parse(jobs=self._jobs(status="in_progress", conclusion=None))

    def test_rejects_completed_failed_source_run(self) -> None:
        with self.assertRaisesRegex(base.ResumeError, "not successful"):
            self._parse(run=self._run(status="completed", conclusion="failure"))

    def test_rejects_artifact_package_laundering(self) -> None:
        other = "QORE-CODEX-5a158ef0fb2e-1111111111111111"
        with self.assertRaisesRegex(base.ResumeError, "request/package binding failed"):
            self._parse(archive=self._archive(request_package=other))

    def test_rejects_production_authority_in_result(self) -> None:
        request = {
            "schema_version": "qore.codex.engineering.request.v1",
            "package_id": self.package,
            "architect_run_id": str(self.architect_run_id),
            "source_main_sha": self.source_sha,
        }
        result = {
            "schema_version": "qore.codex.worker.result.v1",
            "source_main_sha": self.source_sha,
            "status": "BLOCKED",
            "production_authority": True,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("codex-request.json", json.dumps(request))
            bundle.writestr("codex-worker-result.json", json.dumps(result))
        with self.assertRaisesRegex(base.ResumeError, "Production authority"):
            self._parse(archive=buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
