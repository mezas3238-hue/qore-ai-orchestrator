from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import economic_control_plane as eco


class EconomicControlPlaneTests(unittest.TestCase):
    BASE = "a" * 40
    HEAD = "b" * 40
    TREE = "c" * 40
    SYNTHETIC = "d" * 40

    def candidate(self) -> eco.CandidateIdentity:
        return eco.CandidateIdentity(
            repository="mezas3238-hue/qore-core",
            base_sha=self.BASE,
            head_sha=self.HEAD,
            tree_sha=self.TREE,
            synthetic_sha=self.SYNTHETIC,
        )

    def test_candidate_identity_is_hash_bound_and_production_false(self):
        first = self.candidate()
        second = self.candidate()
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertTrue(first.candidate_id.startswith("QORE-CAND-"))
        with self.assertRaisesRegex(ValueError, "production_authority"):
            eco.CandidateIdentity(
                repository="mezas3238-hue/qore-core",
                base_sha=self.BASE,
                head_sha=self.HEAD,
                tree_sha=self.TREE,
                synthetic_sha=self.SYNTHETIC,
                production_authority=True,
            )

    def test_evidence_ledger_reuses_only_exact_candidate_and_inputs(self):
        candidate = self.candidate()
        record = eco.EvidenceRecord(
            candidate_id=candidate.candidate_id,
            evidence_type="QG_EVIDENCE",
            input_digest="input-1",
            tool_version="qg-v1",
            command="ruff && mypy && pytest",
            output_digest="output-1",
            reusable_across_same_head=True,
        )
        ledger = eco.EvidenceLedger([record])
        self.assertEqual(
            ledger.reusable(
                candidate_id=candidate.candidate_id,
                evidence_type="QG_EVIDENCE",
                input_digest="input-1",
            ),
            (record,),
        )
        self.assertEqual(
            ledger.reusable(
                candidate_id="QORE-CAND-other",
                evidence_type="QG_EVIDENCE",
                input_digest="input-1",
            ),
            (),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            ledger.write_jsonl(path)
            self.assertIn(record.evidence_id[:10], record.evidence_id)
            self.assertIn("QG_EVIDENCE", path.read_text(encoding="utf-8"))

    def test_cost_ledger_unifies_observed_and_estimated_cost(self):
        candidate = self.candidate()
        observed = eco.CostEvent(
            session_id="S1",
            actor="SOL",
            model="gpt-5.6",
            stage="ARCHITECT",
            candidate_id=candidate.candidate_id,
            input_tokens=100_000,
            cached_input_tokens=80_000,
            cache_write_tokens=0,
            output_tokens=5_000,
            observed_usd=0.42,
        )
        estimated = eco.CostEvent(
            session_id="S1",
            actor="DEEPSEEK",
            model="deepseek-v4-pro",
            stage="EXPERT",
            candidate_id=candidate.candidate_id,
            input_tokens=50_000,
            cached_input_tokens=10_000,
            cache_write_tokens=0,
            output_tokens=10_000,
        )
        ledger = eco.CostLedger([observed, estimated])
        cards = {
            "deepseek-v4-pro": eco.PriceCard(1.0, 0.1, 0.0, 2.0),
        }
        expected_estimated = (40_000 * 1.0 + 10_000 * 0.1 + 10_000 * 2.0) / 1_000_000
        self.assertAlmostEqual(ledger.total_observed_usd(), 0.42)
        self.assertAlmostEqual(ledger.estimated_total_usd(cards), 0.42 + expected_estimated)

    def test_context_duplication_uses_deterministic_chunk_digests(self):
        ratio = eco.context_duplication_ratio(
            {"a": "abcdabcd", "b": "abcdwxyz"},
            chunk_chars=4,
        )
        self.assertAlmostEqual(ratio, 0.5)
        self.assertEqual(eco.context_duplication_ratio({}, chunk_chars=4), 0.0)

    def test_fable_cost_is_precomputed_without_dispatch(self):
        plan = eco.AuditTokenPlan(
            stable_tokens=500_000,
            changed_tokens=50_000,
            cross_boundary_tokens=50_000,
            expected_output_tokens=20_000,
            cache_hit_ratio=0.9,
            batch_discount=0.5,
        )
        card = eco.PriceCard(
            input_per_million=10.0,
            cached_input_per_million=1.0,
            cache_write_per_million=0.0,
            output_per_million=50.0,
        )
        estimate = eco.estimate_fable_audit_cost(plan, card)
        self.assertEqual(estimate.input_tokens, 600_000)
        self.assertEqual(estimate.cached_input_tokens, 450_000)
        self.assertEqual(estimate.uncached_input_tokens, 150_000)
        self.assertAlmostEqual(estimate.pre_discount_usd, 2.95)
        self.assertAlmostEqual(estimate.estimated_usd, 1.475)

    def test_risk_classifier_is_shadow_only_and_fail_closed_upward(self):
        low = eco.classify_risk_shadow(["docs/design.md"])
        semantic = eco.classify_risk_shadow(["schemas/new_contract.json"])
        authority = eco.classify_risk_shadow(["scripts/security_authority.py"])
        release = eco.classify_risk_shadow(
            ["docs/release.md"], release_or_production_sensitive=True
        )
        self.assertEqual(low.tier, eco.RiskTier.T0)
        self.assertEqual(semantic.tier, eco.RiskTier.T2)
        self.assertEqual(authority.tier, eco.RiskTier.T3)
        self.assertEqual(release.tier, eco.RiskTier.T4)
        self.assertTrue(authority.shadow_only)
        with self.assertRaisesRegex(ValueError, "shadow-only"):
            eco.RiskAssessment(eco.RiskTier.T1, ("x",), shadow_only=False)

    def test_cost_scheduler_forbids_ai_when_deterministic_work_remains(self):
        candidate = self.candidate()
        intent = eco.ModelCallIntent(
            semantic_uncertainty="contract ambiguity",
            model_role="SOL",
            candidate_id=candidate.candidate_id,
            required_evidence=("diff",),
            expected_information_gain="resolve contract",
            estimated_tokens=10_000,
            estimated_usd=0.25,
            remaining_budget_usd=1.0,
            invalidation_rule="HEAD change invalidates decision",
        )
        deterministic = eco.schedule_model_call_shadow(
            intent=intent,
            deterministic_work_pending=True,
            external_agent_pending=False,
            human_authority_required=False,
        )
        call = eco.schedule_model_call_shadow(
            intent=intent,
            deterministic_work_pending=False,
            external_agent_pending=False,
            human_authority_required=False,
        )
        self.assertEqual(deterministic.action, eco.RouteAction.NO_CALL_REQUIRED)
        self.assertEqual(call.action, eco.RouteAction.CALL_MODEL)
        self.assertTrue(call.shadow_only)

    def test_cost_scheduler_stops_before_over_budget_call(self):
        candidate = self.candidate()
        intent = eco.ModelCallIntent(
            semantic_uncertainty="conflict",
            model_role="FABLE",
            candidate_id=candidate.candidate_id,
            required_evidence=("freeze",),
            expected_information_gain="adversarial signal",
            estimated_tokens=1_000_000,
            estimated_usd=12.0,
            remaining_budget_usd=5.0,
            invalidation_rule="candidate change invalidates audit",
        )
        result = eco.schedule_model_call_shadow(
            intent=intent,
            deterministic_work_pending=False,
            external_agent_pending=False,
            human_authority_required=False,
        )
        self.assertEqual(result.action, eco.RouteAction.BUDGET_STOP)

    def test_sol_packet_is_minimal_hash_bound_and_has_no_production_authority(self):
        candidate = self.candidate()
        risk = eco.classify_risk_shadow(["src/semantic_contract.py"])
        packet = eco.build_sol_decision_packet(
            candidate=candidate,
            risk=risk,
            workflow_state="FROZEN",
            last_event="QG_GREEN",
            decision_required="adjudicate one semantic dispute",
            active_contract={"id": "C1"},
            semantic_questions=["is invariant preserved?"],
            changed_files=["src/semantic_contract.py"],
            diff_summary={"files": 1},
            findings={"open": [{"id": "F1"}], "resolved": []},
            qg_summary={"green": True},
            review_summary={},
            source_slices=[{"path": "src/semantic_contract.py", "sha": "x"}],
            budget_remaining_usd=4.0,
            allowed_transitions=["CODEX", "WAIT"],
        )
        self.assertFalse(packet["production_authority"])
        self.assertNotIn("roadmap", packet)
        self.assertTrue(packet["packet_id"].startswith("QORE-SOL-PKT-"))
        packet_again = eco.build_sol_decision_packet(
            candidate=candidate,
            risk=risk,
            workflow_state="FROZEN",
            last_event="QG_GREEN",
            decision_required="adjudicate one semantic dispute",
            active_contract={"id": "C1"},
            semantic_questions=["is invariant preserved?"],
            changed_files=["src/semantic_contract.py"],
            diff_summary={"files": 1},
            findings={"open": [{"id": "F1"}], "resolved": []},
            qg_summary={"green": True},
            review_summary={},
            source_slices=[{"path": "src/semantic_contract.py", "sha": "x"}],
            budget_remaining_usd=4.0,
            allowed_transitions=["CODEX", "WAIT"],
        )
        self.assertEqual(packet["packet_sha256"], packet_again["packet_sha256"])

    def test_codex_capsule_prevents_rediscovery_and_supports_need_evidence(self):
        candidate = self.candidate()
        capsule = eco.build_codex_task_capsule(
            candidate=candidate,
            source_sha=self.HEAD,
            reference_sha=self.BASE,
            changed_file_allowlist=["src/a.py"],
            forbidden_files=["src/authority.py"],
            contract={"objective": "repair bounded defect"},
            findings=[{"id": "F1"}],
            acceptance_tests=["python3 -m unittest tests.test_a"],
            source_slices=[{"symbol": "A"}],
            relevant_tests=["tests/test_a.py"],
            historical_delta={"reference_diff": "materialized"},
        )
        self.assertTrue(capsule["reference_materialized"])
        self.assertEqual(capsule["source_sha"], self.HEAD)
        self.assertEqual(capsule["missing_evidence_protocol"], "NEED_EVIDENCE(symbol/file/test)")
        self.assertFalse(capsule["production_authority"])

    def test_review_freeze_preserves_independence_and_exact_contents(self):
        candidate = self.candidate()
        package = eco.build_review_freeze_package(
            candidate=candidate,
            reviewer_role="DEEPSEEK_EXPERT",
            contract_version="v1",
            changed_file_manifest=[{"path": "src/a.py", "sha": "blob"}],
            exact_diff="diff --git a/src/a.py b/src/a.py",
            changed_file_contents=[{"path": "src/a.py", "content": "x = 1"}],
            semantic_dependency_slices=[{"path": "src/b.py", "content": "class B: pass"}],
            architecture_invariants=["provider-neutral Core"],
            qg_evidence={"green": True},
            adversarial_evidence=[],
            prior_finding_closure=[],
            questions=["find material defects"],
            prohibited_conclusions=["Production ready"],
        )
        self.assertIn("not authority", package["reviewer_independence"])
        self.assertEqual(package["changed_file_contents"][0]["content"], "x = 1")
        self.assertFalse(package["production_authority"])

    def test_migration_safe_review_plan_keeps_all_reviewers(self):
        candidate = self.candidate()
        plan = eco.default_review_plan(candidate.candidate_id, eco.RiskTier.T2)
        self.assertEqual(
            plan.stages,
            ("QG", "DEEPSEEK_EXPERT", "DEEPSEEK_CODER", "CLAUDE", "SOL_FINAL"),
        )
        next_stage = eco.clean_pass_next_stage(
            plan=plan,
            completed_stage="DEEPSEEK_EXPERT",
            verdict="HALLAZGOS: NINGUNO / VALIDACIÓN OK",
            exact_candidate_unchanged=True,
            evidence_complete=True,
            anomaly_present=False,
        )
        self.assertEqual(next_stage, "DEEPSEEK_CODER")
        blocked = eco.clean_pass_next_stage(
            plan=plan,
            completed_stage="DEEPSEEK_EXPERT",
            verdict="FINDING",
            exact_candidate_unchanged=True,
            evidence_complete=True,
            anomaly_present=False,
        )
        self.assertIsNone(blocked)


if __name__ == "__main__":
    unittest.main()
