from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dispatch_reviewer_request_recovery as dispatch  # noqa: E402
import prepare_reviewer_recovery as prepare  # noqa: E402
import trigger_reviewer_recovery as trigger  # noqa: E402


class ReviewerRecoveryTests(unittest.TestCase):
    HEAD = "a" * 40
    QORE_MAIN = "b" * 40

    @staticmethod
    def _zip(files: dict[str, object]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in files.items():
                archive.writestr(name, json.dumps(value))
        return buffer.getvalue()

    @classmethod
    def _decision(cls) -> dict[str, object]:
        return {
            "status": "REVIEW_TASK",
            "next_actor": "DEEPSEEK",
            "source_main_sha": cls.QORE_MAIN,
            "review_contract": {
                "enabled": True,
                "contract_id": "R95",
                "pr_number": 466,
            },
        }

    @classmethod
    def _canonical_source_run(cls) -> dict[str, object]:
        return {
            "id": 33330339041,
            "name": trigger.SOURCE_WORKFLOW_NAME,
            "path": trigger.SOURCE_WORKFLOW_PATH,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": cls.HEAD,
            "status": "completed",
            "conclusion": "failure",
        }

    def test_trigger_accepts_only_canonical_failed_source_path(self) -> None:
        run = self._canonical_source_run()
        trigger.validate_source_run(run, 33330339041, self.HEAD)

        recovery = dict(run)
        recovery["path"] = ".github/workflows/qore-architect-review-recovery-v1.yml"
        with self.assertRaisesRegex(trigger.RecoveryTriggerError, "canonical Autonomous V2"):
            trigger.validate_source_run(recovery, 33330339041, self.HEAD)

    def test_trigger_recovery_needed_only_before_package_or_dispatch(self) -> None:
        source = self._zip({"architect-decision.json": self._decision()})
        self.assertTrue(trigger.recovery_needed(source))

        packaged = self._zip(
            {
                "architect-decision.json": self._decision(),
                "reviewer-package.json": {"package_id": "already"},
            }
        )
        self.assertFalse(trigger.recovery_needed(packaged))

        dispatched = self._zip(
            {
                "architect-decision.json": self._decision(),
                "reviewer-dispatch.json": {"package_id": "already"},
            }
        )
        self.assertFalse(trigger.recovery_needed(dispatched))

    def test_prepare_rejects_source_that_already_contains_reviewer_package(self) -> None:
        archive = self._zip(
            {
                "architect-decision.json": self._decision(),
                "reviewer-package.json": {"package_id": "already"},
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                prepare.RecoveryPrepareError,
                "source already contains reviewer package/dispatch evidence",
            ):
                prepare.extract_allowed(archive, Path(tmp))

    def test_prepare_rejects_snapshot_main_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architect-decision.json").write_text(
                json.dumps(self._decision()), encoding="utf-8"
            )
            (root / "qore-state.json").write_text(
                json.dumps(
                    {
                        "main_sha": "c" * 40,
                        "snapshot_consistent": True,
                        "collection_errors": [],
                    }
                ),
                encoding="utf-8",
            )
            (root / "sol-usage-initial.json").write_text("{}", encoding="utf-8")
            (root / "sol-usage.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(prepare.RecoveryPrepareError, "decision/snapshot binding"):
                prepare.validate_rehydrated(root)

    @staticmethod
    def _prior_candidate() -> tuple[dict[str, object], dict[str, object]]:
        prior = {
            "pr_number": 466,
            "package_id": "QORE-UMI14-CORR-UMI13-001-DS-EXPERT-R94",
            "expected_head": "d" * 40,
            "expected_synthetic": "e" * 40,
            "review_mode": "expert",
        }
        candidate = {
            "pr_number": 466,
            "package_id": "QORE-SOL-bbbbbbbbbbbb-DS-EXPERT-R123",
            "expected_head": "d" * 40,
            "expected_synthetic": "e" * 40,
            "review_mode": "expert",
        }
        return prior, candidate

    def test_deepseek_terminal_failed_equivalent_can_be_superseded_once(self) -> None:
        prior, candidate = self._prior_candidate()
        run = {"id": 10, "status": "completed", "conclusion": "failure"}
        with (
            patch.object(dispatch, "bound_runs_for_package", return_value=[run]),
            patch.object(dispatch, "semantic_publication_exists", return_value=False),
        ):
            retryable, observed = dispatch.terminal_failed_equivalent_is_retryable(
                dispatch.DEEPSEEK_REPO, prior, candidate, "token"
            )
        self.assertTrue(retryable)
        self.assertIs(observed, run)

    def test_equivalent_supersession_rejects_active_success_published_and_claude(self) -> None:
        prior, candidate = self._prior_candidate()
        cases = (
            ({"id": 10, "status": "in_progress", "conclusion": None}, False),
            ({"id": 10, "status": "completed", "conclusion": "success"}, False),
        )
        for run, expected in cases:
            with self.subTest(run=run):
                with (
                    patch.object(dispatch, "bound_runs_for_package", return_value=[run]),
                    patch.object(dispatch, "semantic_publication_exists", return_value=False),
                ):
                    retryable, _ = dispatch.terminal_failed_equivalent_is_retryable(
                        dispatch.DEEPSEEK_REPO, prior, candidate, "token"
                    )
                self.assertEqual(retryable, expected)

        failed = {"id": 10, "status": "completed", "conclusion": "failure"}
        with (
            patch.object(dispatch, "bound_runs_for_package", return_value=[failed]),
            patch.object(dispatch, "semantic_publication_exists", return_value=True),
        ):
            retryable, _ = dispatch.terminal_failed_equivalent_is_retryable(
                dispatch.DEEPSEEK_REPO, prior, candidate, "token"
            )
        self.assertFalse(retryable)

        claude_prior = dict(prior)
        claude_candidate = dict(candidate)
        with patch.object(dispatch, "bound_runs_for_package") as bound:
            retryable, observed = dispatch.terminal_failed_equivalent_is_retryable(
                dispatch.CLAUDE_REPO, claude_prior, claude_candidate, "token"
            )
        self.assertFalse(retryable)
        self.assertIsNone(observed)
        bound.assert_not_called()

    def test_semantic_publication_requires_exact_deepseek_marker(self) -> None:
        package = "QORE-UMI14-CORR-UMI13-001-DS-EXPERT-R94"
        head = "d" * 40
        exact = f"<!-- QORE-DEEPSEEK-REVIEW package={package} head={head} -->"

        with patch.object(
            dispatch,
            "request_json",
            side_effect=[
                [{"body": f"Adjudicated historical {package}; no reviewer publication."}],
                [],
                [],
            ],
        ):
            self.assertFalse(dispatch.semantic_publication_exists(466, package, head, "token"))

        with patch.object(
            dispatch,
            "request_json",
            side_effect=[[{"body": exact}], [], []],
        ):
            self.assertTrue(dispatch.semantic_publication_exists(466, package, head, "token"))

    def test_workflows_pin_no_model_recovery_path_isolation_and_qg_auth(self) -> None:
        root = Path(__file__).resolve().parents[1]
        trigger_workflow = (root / ".github/workflows/qore-reviewer-recovery-trigger.yml").read_text(
            encoding="utf-8"
        )
        recovery_workflow = (
            root / ".github/workflows/qore-architect-review-recovery-v1.yml"
        ).read_text(encoding="utf-8")
        ordinary_workflow = (
            root / ".github/workflows/qore-architect-autonomous-v2.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("actions: write", trigger_workflow)
        self.assertIn(
            "github.event.workflow_run.path == '.github/workflows/qore-architect-autonomous-v2.yml'",
            trigger_workflow,
        )
        self.assertIn("name: QORE Architect autonomous V2", recovery_workflow)
        self.assertIn(
            "QORE_CORE_READ_TOKEN: ${{ secrets.QORE_REVIEWER_DISPATCH_TOKEN }}",
            recovery_workflow,
        )
        self.assertIn("qore-architect-v2-${{ github.run_id }}", recovery_workflow)
        self.assertNotIn("OPENAI_SOL_API_KEY", recovery_workflow)
        self.assertIn(
            "QORE_CORE_READ_TOKEN: ${{ secrets.QORE_REVIEWER_DISPATCH_TOKEN }}",
            ordinary_workflow,
        )
        self.assertIn('test -n "$QORE_CORE_READ_TOKEN"', ordinary_workflow)


if __name__ == "__main__":
    unittest.main()
