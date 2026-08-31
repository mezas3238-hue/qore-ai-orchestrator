from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_preauthorized_review_chain as chain_builder
import validate_preauthorized_chain_callback as target


class PreauthorizedChainCallbackTests(unittest.TestCase):
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

    def decision(self):
        return {
            "schema_version": "qore.architect.decision.v1",
            "source_main_sha": "a" * 40,
            "engineering_contract": {
                "enabled": True,
                "contract_id": "ENG-1",
                "target_repository": "mezas3238-hue/qore-core",
                "objective": "bounded fix",
                "scope": ["src/qore/a.py"],
                "acceptance": ["full QG"],
                "required_tests": [],
                "forbidden": ["no Production"],
            },
            "production_authority": False,
        }

    def test_exact_chain_recomputes_from_original_architect_run(self):
        chain = chain_builder.build_chain(
            candidate_binding=self.candidate(),
            engineering_contract=self.decision()["engineering_contract"],
            source_architect_run_id=123,
        )
        package = chain["stages"][0]["package_id"]
        prompt = (
            f"<!-- QORE-PREAUTHORIZED-REVIEW-CHAIN id={chain['chain_id']} "
            f"sha={chain['chain_sha256']} stage=DEEPSEEK_EXPERT -->\n"
        )
        result = target.validate_chain_callback(
            package_id=package,
            prompt_text=prompt,
            candidate_binding=self.candidate(),
            source_architect_decision=self.decision(),
        )
        self.assertEqual(result["source_architect_run_id"], 123)
        self.assertEqual(result["stage"], "DEEPSEEK_EXPERT")
        self.assertFalse(result["production_authority"])

    def test_tampered_chain_marker_fails_closed(self):
        chain = chain_builder.build_chain(
            candidate_binding=self.candidate(),
            engineering_contract=self.decision()["engineering_contract"],
            source_architect_run_id=123,
        )
        package = chain["stages"][0]["package_id"]
        prompt = (
            f"<!-- QORE-PREAUTHORIZED-REVIEW-CHAIN id={chain['chain_id']} "
            f"sha={'f' * 64} stage=DEEPSEEK_EXPERT -->"
        )
        with self.assertRaisesRegex(ValueError, "does not recompute"):
            target.validate_chain_callback(
                package_id=package,
                prompt_text=prompt,
                candidate_binding=self.candidate(),
                source_architect_decision=self.decision(),
            )

    def test_disabled_engineering_contract_cannot_create_chain_authority(self):
        decision = self.decision()
        decision["engineering_contract"]["enabled"] = False
        chain = chain_builder.build_chain(
            candidate_binding=self.candidate(),
            engineering_contract=self.decision()["engineering_contract"],
            source_architect_run_id=123,
        )
        package = chain["stages"][0]["package_id"]
        with self.assertRaisesRegex(ValueError, "did not preauthorize"):
            target.validate_chain_callback(
                package_id=package,
                prompt_text="x",
                candidate_binding=self.candidate(),
                source_architect_decision=decision,
            )


if __name__ == "__main__":
    unittest.main()
