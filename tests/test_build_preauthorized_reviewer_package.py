from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_preauthorized_reviewer_package as target


class PreauthorizedReviewerPackageTests(unittest.TestCase):
    BASE = "a" * 40
    HEAD = "b" * 40
    SYNTHETIC = "c" * 40

    def chain(self):
        return {
            "schema_version": "qore.preauthorized.review.chain.v1",
            "chain_id": "QORE-REVIEW-CHAIN-123",
            "chain_sha256": "d" * 64,
            "candidate_id": "QORE-CAND-1",
            "candidate": {
                "repository": "mezas3238-hue/qore-core",
                "pull_request_number": 466,
                "base_sha": self.BASE,
                "head_sha": self.HEAD,
                "tree_sha": "e" * 40,
                "synthetic_sha": self.SYNTHETIC,
            },
            "source_architect_run_id": 123,
            "engineering_contract": {
                "enabled": True,
                "contract_id": "ENG-1",
                "target_repository": "mezas3238-hue/qore-core",
                "objective": "bounded semantic fix",
                "scope": ["src/qore/a.py"],
                "acceptance": ["full QG"],
                "required_tests": [],
                "forbidden": ["no Production"],
            },
            "stages": [
                {"stage": "DEEPSEEK_EXPERT", "actor": "DEEPSEEK", "review_kind": "DEEPSEEK_EXPERT", "package_id": "QORE-SOL-aaaaaaaaaaaa-DS-EXPERT-R123"},
                {"stage": "DEEPSEEK_CODER", "actor": "DEEPSEEK", "review_kind": "DEEPSEEK_CODER", "package_id": "QORE-SOL-aaaaaaaaaaaa-DS-CODER-R123"},
                {"stage": "CLAUDE", "actor": "CLAUDE_CODE", "review_kind": "CLAUDE_TECHNICAL", "package_id": "QORE-SOL-aaaaaaaaaaaa-CLAUDE-R123"},
                {"stage": "SOL_FINAL", "actor": "SOL", "review_kind": "FINAL_ADJUDICATION", "package_id": None},
            ],
            "final_sol_required": True,
            "reviewer_suppression": False,
            "production_authority": False,
        }

    def qg(self):
        return target.legacy.QualitySummary(
            run_id=1,
            job_id=2,
            ruff_passed=True,
            mypy_source_files=10,
            pytest_collected=20,
            pytest_passed=20,
            pytest_warnings=0,
            coverage_total_statements=100,
            coverage_missed_statements=10,
            coverage_percent=90,
        )

    @patch.object(target.legacy, "resolve_quality")
    @patch.object(target.legacy, "resolve_freeze")
    @patch.object(target, "_reviews")
    def test_expert_package_contains_chain_marker(self, reviews, freeze, quality):
        freeze.return_value = (self.BASE, self.HEAD, self.SYNTHETIC)
        quality.return_value = self.qg()
        reviews.return_value = []
        prompt, request, metadata = target.build_stage_package(
            chain=self.chain(), stage_name="DEEPSEEK_EXPERT"
        )
        self.assertIn("QORE-PREAUTHORIZED-REVIEW-CHAIN", prompt)
        self.assertEqual(request["review_mode"], "expert")
        self.assertEqual(metadata["stage"], "DEEPSEEK_EXPERT")
        self.assertFalse(metadata["production_authority"])

    @patch.object(target.legacy, "resolve_quality")
    @patch.object(target.legacy, "resolve_freeze")
    @patch.object(target, "_reviews")
    @patch.object(target, "deepseek_review_from_pr_reviews")
    def test_coder_requires_clean_expert(self, verdict, reviews, freeze, quality):
        freeze.return_value = (self.BASE, self.HEAD, self.SYNTHETIC)
        quality.return_value = self.qg()
        reviews.return_value = []
        verdict.return_value.verdict = target.VerdictClass.BLOCKED
        with self.assertRaisesRegex(ValueError, "Expert is not exact-head clean"):
            target.build_stage_package(chain=self.chain(), stage_name="DEEPSEEK_CODER")

    @patch.object(target.legacy, "resolve_quality")
    @patch.object(target.legacy, "resolve_freeze")
    @patch.object(target, "_reviews")
    @patch.object(target, "deepseek_review_from_pr_reviews")
    def test_claude_requires_expert_and_coder_clean(self, verdict, reviews, freeze, quality):
        freeze.return_value = (self.BASE, self.HEAD, self.SYNTHETIC)
        quality.return_value = self.qg()
        reviews.return_value = []
        verdict.return_value.verdict = target.VerdictClass.CLEAN
        _, request, metadata = target.build_stage_package(chain=self.chain(), stage_name="CLAUDE")
        self.assertIn("qg", request)
        self.assertEqual(metadata["target_repo"], "mezas3238-hue/qore-claude-reviewer")
        self.assertEqual(verdict.call_count, 2)

    @patch.object(target.legacy, "resolve_freeze")
    def test_moved_candidate_fails_closed(self, freeze):
        freeze.return_value = (self.BASE, "f" * 40, self.SYNTHETIC)
        with self.assertRaisesRegex(ValueError, "no longer matches"):
            target.build_stage_package(chain=self.chain(), stage_name="DEEPSEEK_EXPERT")


if __name__ == "__main__":
    unittest.main()
