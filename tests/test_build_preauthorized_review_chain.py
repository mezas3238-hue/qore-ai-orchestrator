from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_preauthorized_review_chain as target


class PreauthorizedReviewChainTests(unittest.TestCase):
    def candidate(self):
        return {
            "schema_version": "qore.candidate.binding.api.v1",
            "candidate_id": "QORE-CAND-1",
            "repository": "mezas3238-hue/qore-core",
            "pull_request_number": 466,
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "tree_sha": "c" * 40,
            "synthetic_sha": "d" * 40,
            "production_authority": False,
        }

    def contract(self):
        return {
            "enabled": True,
            "contract_id": "ENG-1",
            "target_repository": "mezas3238-hue/qore-core",
            "objective": "bounded fix",
            "scope": ["src/qore/a.py"],
            "acceptance": ["full QG"],
            "required_tests": [],
            "forbidden": ["no Production"],
        }

    def test_full_chain_is_predeclared(self):
        chain = target.build_chain(
            candidate_binding=self.candidate(),
            engineering_contract=self.contract(),
            source_architect_run_id=123,
        )
        self.assertEqual(
            [stage["stage"] for stage in chain["stages"]],
            ["DEEPSEEK_EXPERT", "DEEPSEEK_CODER", "CLAUDE", "SOL_FINAL"],
        )
        self.assertTrue(chain["final_sol_required"])
        self.assertFalse(chain["reviewer_suppression"])
        self.assertFalse(chain["production_authority"])
        self.assertTrue(chain["chain_id"].startswith("QORE-REVIEW-CHAIN-"))

    def test_package_ids_keep_original_architect_root(self):
        chain = target.build_chain(
            candidate_binding=self.candidate(),
            engineering_contract=self.contract(),
            source_architect_run_id=987,
        )
        self.assertTrue(chain["stages"][0]["package_id"].endswith("R987"))
        self.assertTrue(chain["stages"][1]["package_id"].endswith("R987"))
        self.assertTrue(chain["stages"][2]["package_id"].endswith("R987"))

    def test_production_authority_fails_closed(self):
        candidate = self.candidate()
        candidate["production_authority"] = True
        with self.assertRaisesRegex(ValueError, "Production"):
            target.build_chain(
                candidate_binding=candidate,
                engineering_contract=self.contract(),
                source_architect_run_id=1,
            )


if __name__ == "__main__":
    unittest.main()
