from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fable_audit_executor as executor
import fable_audit_package_v2 as target


class FakeAdapter:
    def __init__(self):
        self.calls = 0

    def audit(self, package):
        self.calls += 1
        return {
            "findings": [
                {"finding_id": "F-2", "deterministic_status": "UNVERIFIED"},
                {"finding_id": "F-1", "deterministic_status": "REPRODUCED"},
            ],
            "usage": {"input_tokens": 100},
        }


class FableAuditPackageV2Tests(unittest.TestCase):
    def shadow_package(self):
        return {
            "schema_version": "qore.fable.audit.package.v1",
            "audit_mode": "DELTA",
            "audit_reasons": ["tier2_semantic_change"],
            "system_freeze": {},
            "primary_candidate_id": "QORE-CAND-1",
            "primary_candidate": {"production_authority": False},
            "changed_since_last_audit": [],
            "dependency_graph": {},
            "authority_graph": {},
            "trust_boundaries": [],
            "data_flows": [],
            "ai_orchestration_graph": {},
            "contracts": [],
            "invariants": [],
            "forbidden_transitions": [],
            "qg_evidence": [],
            "known_attack_surfaces": [],
            "source_index": [],
            "symbol_index": [],
            "cross_component_interfaces": [],
            "prior_audit_evidence_refs": [],
            "hard_budget_usd": 1.0,
            "instructions": "falsify",
            "output_contract": ["FINDING_ID"],
            "production_authority": False,
            "shadow_only": True,
            "package_sha256": "a" * 64,
            "package_id": "QORE-FABLE-AUDIT-" + "a" * 24,
        }

    def preflight(self, within=True):
        return {
            "within_budget": within,
            "estimated_usd": 0.2,
            "hard_budget_usd": 1.0,
        }

    def policy(self):
        return {
            "mode": "LIMITED_LIVE",
            "delta_live": True,
            "cross_boundary_live": True,
            "full_system_live": True,
            "reviewer_substitution": False,
        }

    def test_limited_live_requires_activation_and_budget(self):
        package = target.build_fable_audit_package_v2(
            shadow_package=self.shadow_package(),
            execution_mode="LIMITED_LIVE",
            activation_policy=self.policy(),
            cost_preflight=self.preflight(),
        )
        self.assertEqual(package["execution_mode"], "LIMITED_LIVE")
        self.assertFalse(package["reviewer_substitution"])
        self.assertFalse(package["production_authority"])
        self.assertTrue(package["package_id"].startswith("QORE-FABLE-AUDIT-V2-"))

    def test_over_budget_live_execution_is_rejected_before_adapter(self):
        with self.assertRaisesRegex(ValueError, "exceeds budget"):
            target.build_fable_audit_package_v2(
                shadow_package=self.shadow_package(),
                execution_mode="LIMITED_LIVE",
                activation_policy=self.policy(),
                cost_preflight=self.preflight(False),
            )

    def test_executor_compacts_findings_and_keeps_final_sol(self):
        package = target.build_fable_audit_package_v2(
            shadow_package=self.shadow_package(),
            execution_mode="CONTROLLED_VALIDATION",
            activation_policy=None,
            cost_preflight=self.preflight(),
        )
        adapter = FakeAdapter()
        result = executor.execute_fable_audit(package=package, adapter=adapter)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(result.raw_finding_count, 2)
        self.assertEqual(len(result.compacted_findings["REPRODUCED"]), 1)
        self.assertTrue(result.final_sol_adjudication_required)
        self.assertFalse(result.reviewer_substitution)
        self.assertFalse(result.production_authority)

    def test_shadow_package_cannot_call_provider(self):
        package = target.build_fable_audit_package_v2(
            shadow_package=self.shadow_package(),
            execution_mode="SHADOW",
            activation_policy=None,
            cost_preflight=self.preflight(),
        )
        adapter = FakeAdapter()
        with self.assertRaisesRegex(ValueError, "controlled or limited-live"):
            executor.execute_fable_audit(package=package, adapter=adapter)
        self.assertEqual(adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
