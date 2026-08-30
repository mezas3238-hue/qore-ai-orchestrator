from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_reviewer_package_frozen_qg as frozen  # noqa: E402


class FrozenQGReuseTests(unittest.TestCase):
    BASE = "a" * 40
    HEAD = "b" * 40
    SYNTHETIC = "c" * 40

    @classmethod
    def qg(cls) -> dict[str, object]:
        return {
            "run_id": 33283252638,
            "job_id": 99181893347,
            "ruff_passed": True,
            "mypy_source_files": 741,
            "pytest_collected": 4887,
            "pytest_passed": 4887,
            "pytest_warnings": 7,
            "coverage_total_statements": 47615,
            "coverage_missed_statements": 6235,
            "coverage_percent": 87,
        }

    @classmethod
    def snapshot(cls, qg=None) -> dict[str, object]:  # type: ignore[no-untyped-def]
        request = {
            "pr_number": 466,
            "expected_base": cls.BASE,
            "expected_head": cls.HEAD,
            "expected_synthetic": cls.SYNTHETIC,
            "qg_summary": cls.qg() if qg is None else qg,
        }
        return {
            "open_pull_requests": [
                {
                    "number": 466,
                    "base_sha": cls.BASE,
                    "head_sha": cls.HEAD,
                    "synthetic_sha": cls.SYNTHETIC,
                }
            ],
            "external_reviewer_state": {
                "deepseek": {"current_request": request},
                "claude": {"current_request": None},
            },
        }

    def test_exact_prior_summary_is_reused(self) -> None:
        value = frozen.resolve_frozen_summary(
            self.snapshot(),
            pr_number=466,
            base=self.BASE,
            head=self.HEAD,
            synthetic=self.SYNTHETIC,
        )
        self.assertEqual(value, self.qg())

    def test_freeze_mismatch_and_bad_summary_fail_closed(self) -> None:
        with self.assertRaisesRegex(frozen.FrozenQGError, "no exact frozen"):
            frozen.resolve_frozen_summary(
                self.snapshot(),
                pr_number=466,
                base=self.BASE,
                head="d" * 40,
                synthetic=self.SYNTHETIC,
            )

        bad = self.qg()
        bad["pytest_passed"] = 4886
        with self.assertRaisesRegex(frozen.FrozenQGError, "not all-pass"):
            frozen.resolve_frozen_summary(
                self.snapshot(bad),
                pr_number=466,
                base=self.BASE,
                head=self.HEAD,
                synthetic=self.SYNTHETIC,
            )

    def test_conflicting_exact_summaries_fail_closed(self) -> None:
        snapshot = self.snapshot()
        other = self.qg()
        other["coverage_percent"] = 86
        snapshot["external_reviewer_state"]["claude"] = {  # type: ignore[index]
            "current_request": {
                "pr_number": 466,
                "expected_base": self.BASE,
                "expected_head": self.HEAD,
                "expected_synthetic": self.SYNTHETIC,
                "qg_summary": other,
            }
        }
        with self.assertRaisesRegex(frozen.FrozenQGError, "conflicting"):
            frozen.resolve_frozen_summary(
                snapshot,
                pr_number=466,
                base=self.BASE,
                head=self.HEAD,
                synthetic=self.SYNTHETIC,
            )

    def test_live_qg_identity_requires_exact_successful_run_and_job(self) -> None:
        qg = self.qg()
        run = {
            "id": qg["run_id"],
            "workflow_id": frozen.package.QORE_CI_WORKFLOW_ID,
            "name": frozen.package.QORE_CI_WORKFLOW_NAME,
            "path": frozen.package.QORE_CI_WORKFLOW_PATH,
            "event": "pull_request",
            "head_sha": self.HEAD,
            "status": "completed",
            "conclusion": "success",
        }
        jobs = {
            "jobs": [
                {
                    "id": qg["job_id"],
                    "run_id": qg["run_id"],
                    "name": "quality",
                    "head_sha": self.HEAD,
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
        with patch.object(frozen.package, "api_json", side_effect=[run, jobs]):
            frozen.validate_live_qg_identity(qg, head=self.HEAD)

        failed_run = dict(run, conclusion="failure")
        with patch.object(frozen.package, "api_json", return_value=failed_run):
            with self.assertRaisesRegex(frozen.FrozenQGError, "no longer matches"):
                frozen.validate_live_qg_identity(qg, head=self.HEAD)


if __name__ == "__main__":
    unittest.main()
