from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import economic_control_plane as eco
import fable_audit_control as fable


class FableAuditControlTests(unittest.TestCase):
    def candidate(self) -> eco.CandidateIdentity:
        return eco.CandidateIdentity(
            repository="mezas3238-hue/qore-core",
            base_sha="a" * 40,
            head_sha="b" * 40,
            tree_sha="c" * 40,
            synthetic_sha="d" * 40,
        )

    def test_tier0_and_tier1_do_not_select_fable(self):
        for tier in (eco.RiskTier.T0, eco.RiskTier.T1):
            selection = fable.select_fable_audit_shadow(
                risk_tier=tier,
                milestone_freeze=False,
                release_recertification=False,
                security_or_governance_change=False,
                cross_boundary_change=False,
            )
            self.assertEqual(selection.mode, fable.FableAuditMode.NONE)
            self.assertTrue(selection.shadow_only)

    def test_tier2_selects_delta_and_tier3_selects_cross_boundary(self):
        delta = fable.select_fable_audit_shadow(
            risk_tier=eco.RiskTier.T2,
            milestone_freeze=False,
            release_recertification=False,
            security_or_governance_change=False,
            cross_boundary_change=False,
        )
        cross = fable.select_fable_audit_shadow(
            risk_tier=eco.RiskTier.T3,
            milestone_freeze=False,
            release_recertification=False,
            security_or_governance_change=True,
            cross_boundary_change=False,
        )
        self.assertEqual(delta.mode, fable.FableAuditMode.DELTA)
        self.assertEqual(cross.mode, fable.FableAuditMode.CROSS_BOUNDARY)

    def test_milestone_or_release_escalates_to_full_system(self):
        selection = fable.select_fable_audit_shadow(
            risk_tier=eco.RiskTier.T1,
            milestone_freeze=True,
            release_recertification=False,
            security_or_governance_change=False,
            cross_boundary_change=False,
        )
        self.assertEqual(selection.mode, fable.FableAuditMode.FULL_SYSTEM)

    def test_cost_gate_can_block_before_any_dispatch(self):
        gate = fable.preflight_fable_cost_shadow(
            token_plan=eco.AuditTokenPlan(
                stable_tokens=1_000_000,
                changed_tokens=100_000,
                cross_boundary_tokens=100_000,
                expected_output_tokens=50_000,
                cache_hit_ratio=0.0,
            ),
            price_card=eco.PriceCard(10.0, 1.0, 0.0, 50.0),
            hard_budget_usd=5.0,
        )
        self.assertFalse(gate.within_budget)
        self.assertGreater(gate.estimate.estimated_usd, gate.hard_budget_usd)
        self.assertTrue(gate.shadow_only)

    def test_fable_package_is_hash_bound_independent_and_nonproduction(self):
        selection = fable.select_fable_audit_shadow(
            risk_tier=eco.RiskTier.T3,
            milestone_freeze=False,
            release_recertification=False,
            security_or_governance_change=True,
            cross_boundary_change=True,
        )
        package = fable.build_fable_audit_package(
            selection=selection,
            system_freeze={
                "qore-core": {"head_sha": "b" * 40, "tree_sha": "c" * 40},
                "orchestrator": {"head_sha": "e" * 40, "tree_sha": "f" * 40},
            },
            primary_candidate=self.candidate(),
            changed_since_last_audit=[{"path": "src/a.py", "sha": "blob"}],
            dependency_graph={"nodes": ["core", "adapter"]},
            authority_graph={"edges": []},
            trust_boundaries=[{"name": "review callback"}],
            data_flows=[{"from": "core", "to": "adapter"}],
            ai_orchestration_graph={"actors": ["SOL", "CODEX"]},
            contracts=[{"id": "C1"}],
            invariants=["provider-neutral Core"],
            forbidden_transitions=["TEST_DEMO_TO_PRODUCTION"],
            qg_evidence=[{"green": True}],
            known_attack_surfaces=["callback forgery"],
            source_index=[{"path": "src/a.py", "sha": "blob"}],
            symbol_index=[{"symbol": "A", "path": "src/a.py"}],
            cross_component_interfaces=[{"name": "boundary"}],
            prior_audit_evidence_refs=["QORE-EVID-1"],
            hard_budget_usd=10.0,
        )
        self.assertEqual(package["audit_mode"], "CROSS_BOUNDARY")
        self.assertFalse(package["production_authority"])
        self.assertTrue(package["shadow_only"])
        self.assertIn("Do not trust prior conclusions", package["instructions"])
        self.assertIn("REPRODUCIBLE_WITNESS", package["output_contract"])
        self.assertTrue(package["package_id"].startswith("QORE-FABLE-AUDIT-"))

    def test_finding_compaction_prepares_one_sol_adjudication(self):
        grouped = fable.compact_fable_findings(
            [
                {"finding_id": "F2", "deterministic_status": "SEMANTIC_DISPUTE"},
                {"finding_id": "F1", "deterministic_status": "REPRODUCED"},
                {"finding_id": "F3", "deterministic_status": "DUPLICATE"},
                {"finding_id": "F4", "deterministic_status": "DISPROVED"},
            ]
        )
        self.assertEqual(grouped["REPRODUCED"][0]["finding_id"], "F1")
        self.assertEqual(grouped["SEMANTIC_DISPUTE"][0]["finding_id"], "F2")
        self.assertEqual(len(grouped["UNVERIFIED"]), 0)


if __name__ == "__main__":
    unittest.main()
