from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_work_unit_shadow as bridge


class BuildWorkUnitShadowTests(unittest.TestCase):
    MAIN = "a" * 40
    TREE = "b" * 40

    def context(self):
        return {
            "schema_version": "qore.model.context.v1",
            "dynamic_context": {
                "repository": "mezas3238-hue/qore-core",
                "source_main_sha": self.MAIN,
                "live_main_sha": self.MAIN,
                "tree_sha": self.TREE,
                "snapshot_consistent": True,
            },
        }

    def decision(self):
        return {
            "schema_version": "qore.architect.decision.v1",
            "source_main_sha": self.MAIN,
            "status": "ENGINEERING_TASK",
            "next_actor": "CODEX",
            "engineering_contract": {
                "enabled": True,
                "contract_id": "ENG-1",
                "target_repository": "mezas3238-hue/qore-core",
                "objective": "bounded fix",
                "scope": ["one bounded unit"],
                "acceptance": ["tests pass"],
                "required_tests": ["full quality gate"],
                "forbidden": ["Production"],
            },
            "evidence_requests": [],
            "risk_gates": ["NO_PRODUCTION"],
            "production_authority": False,
        }

    def test_existing_artifacts_project_to_exact_work_unit(self):
        result = bridge.build_work_unit_from_existing(
            model_context=self.context(), architect_decision=self.decision()
        )
        self.assertTrue(result["work_unit_id"].startswith("QORE-WORK-"))
        self.assertEqual(result["work_unit"]["source_main_sha"], self.MAIN)
        self.assertEqual(result["work_unit"]["source_tree_sha"], self.TREE)
        self.assertEqual(result["next_actor"], "CODEX")
        self.assertTrue(result["shadow_only"])
        self.assertFalse(result["production_authority"])

    def test_inconsistent_snapshot_fails_closed(self):
        context = self.context()
        context["dynamic_context"]["snapshot_consistent"] = False
        with self.assertRaisesRegex(ValueError, "not exact"):
            bridge.build_work_unit_from_existing(
                model_context=context, architect_decision=self.decision()
            )

    def test_decision_source_mismatch_fails_closed(self):
        decision = self.decision()
        decision["source_main_sha"] = "c" * 40
        with self.assertRaisesRegex(ValueError, "mismatch"):
            bridge.build_work_unit_from_existing(
                model_context=self.context(), architect_decision=decision
            )

    def test_production_authority_fails_closed(self):
        decision = self.decision()
        decision["production_authority"] = True
        with self.assertRaisesRegex(ValueError, "Production"):
            bridge.build_work_unit_from_existing(
                model_context=self.context(), architect_decision=decision
            )


if __name__ == "__main__":
    unittest.main()
