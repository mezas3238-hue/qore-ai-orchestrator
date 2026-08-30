from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_reviewer_package
import collect_external_reviewer_state
import dispatch_reviewer_request
import evaluate_sol_escalation
import run_sol_architect
import select_sol_reasoning


class ReasoningPolicyTests(unittest.TestCase):
    def snapshot(self, **overrides):
        base = {
            "snapshot_consistent": True,
            "collection_errors": [],
            "open_pull_requests": [],
            "open_issues": [],
            "recent_main_action_runs": [],
            "recent_commits": [],
        }
        base.update(overrides)
        return base

    def test_routine_state_uses_medium(self):
        policy = select_sol_reasoning.choose_effort(self.snapshot(), "auto")
        self.assertEqual(policy["selected_effort"], "medium")

    def test_material_open_pr_uses_high(self):
        policy = select_sol_reasoning.choose_effort(
            self.snapshot(open_pull_requests=[{"title": "Bounded implementation"}]), "auto"
        )
        self.assertEqual(policy["selected_effort"], "high")

    def test_failover_signal_uses_xhigh(self):
        policy = select_sol_reasoning.choose_effort(
            self.snapshot(open_issues=[{"title": "Failover fencing contract", "labels": []}]), "auto"
        )
        self.assertEqual(policy["selected_effort"], "xhigh")

    def test_security_signal_uses_max(self):
        policy = select_sol_reasoning.choose_effort(
            self.snapshot(open_issues=[{"title": "Security credential boundary", "labels": []}]), "auto"
        )
        self.assertEqual(policy["selected_effort"], "max")

    def test_reviewer_finding_uses_xhigh(self):
        policy = select_sol_reasoning.choose_effort(
            self.snapshot(
                external_reviewer_state={
                    "claude": {"review": {"verdict": "FINDINGS", "text": "VALIDACIÓN NO OK"}}
                }
            ),
            "auto",
        )
        self.assertEqual(policy["selected_effort"], "xhigh")

    def test_explicit_override_is_respected(self):
        policy = select_sol_reasoning.choose_effort(self.snapshot(), "xhigh")
        self.assertEqual(policy["selected_effort"], "xhigh")


class EscalationTests(unittest.TestCase):
    def test_no_signal_does_not_escalate(self):
        decision = {
            "status": "ENGINEERING_TASK",
            "reasoning_assessment": {
                "effort_used": "high",
                "escalation_requested": False,
                "target_effort": "high",
                "reason": "sufficient",
            },
            "risk_gates": [],
        }
        result = evaluate_sol_escalation.choose_escalation(decision, "high")
        self.assertFalse(result["escalate"])
        self.assertEqual(result["target_effort"], "high")

    def test_human_gate_escalates_to_max(self):
        decision = {
            "status": "HUMAN_DECISION_REQUIRED",
            "reasoning_assessment": {
                "effort_used": "high",
                "escalation_requested": False,
                "target_effort": "high",
                "reason": "initial",
            },
            "risk_gates": [],
        }
        result = evaluate_sol_escalation.choose_escalation(decision, "high")
        self.assertTrue(result["escalate"])
        self.assertEqual(result["target_effort"], "max")


class ReviewerRoutingTests(unittest.TestCase):
    def disabled_engineering(self):
        return {
            "enabled": False,
            "contract_id": "",
            "objective": "",
            "scope": [],
            "acceptance": [],
            "required_tests": [],
            "forbidden": [],
        }

    def valid_claude_decision(self):
        return {
            "schema_version": "qore.architect.decision.v1",
            "source_main_sha": "a" * 40,
            "status": "REVIEW_TASK",
            "reasoning_assessment": {
                "effort_used": "high",
                "escalation_requested": False,
                "target_effort": "high",
                "reason": "sufficient",
            },
            "roadmap_anchor": {"path": "docs/roadmap/x.md", "work_package": "x", "reason": "x"},
            "decision": "review",
            "next_actor": "CLAUDE_CODE",
            "engineering_contract": self.disabled_engineering(),
            "review_contract": {
                "enabled": True,
                "contract_id": "review-1",
                "pr_number": 10,
                "review_kind": "CLAUDE_TECHNICAL",
                "objective": "review exact candidate",
                "scope": ["changed files"],
                "adversarial_foci": ["runtime semantics"],
                "acceptance": ["reproducible findings only"],
                "forbidden": ["no writes"],
            },
            "evidence": [],
            "evidence_requests": [],
            "risk_gates": [],
            "production_authority": False,
        }

    def test_claude_review_contract_is_valid(self):
        run_sol_architect.validate_decision(self.valid_claude_decision(), "a" * 40, "high")

    def test_claude_wrong_kind_fails_closed(self):
        decision = self.valid_claude_decision()
        decision["review_contract"]["review_kind"] = "DEEPSEEK_EXPERT"
        with self.assertRaises(ValueError):
            run_sol_architect.validate_decision(decision, "a" * 40, "high")

    def test_deepseek_coder_requires_exact_head_expert_marker(self):
        head = "b" * 40
        snapshot = {
            "open_pull_requests": [
                {
                    "number": 22,
                    "reviews": [
                        {
                            "commit_id": head,
                            "body": f"<!-- QORE-DEEPSEEK-REVIEW package=QORE-X-DS-EXPERT-R1 head={head} -->",
                        }
                    ],
                    "conversation_comments": [],
                }
            ]
        }
        self.assertTrue(build_reviewer_package.has_exact_deepseek_expert(snapshot, 22, head))
        self.assertFalse(build_reviewer_package.has_exact_deepseek_expert(snapshot, 22, "c" * 40))

    def test_claude_equivalent_current_request_is_detected(self):
        prior = {
            "pr_number": 466,
            "expected_head": "a" * 40,
            "expected_synthetic": "b" * 40,
            "package_id": "OLD",
        }
        candidate = dict(prior, package_id="NEW")
        self.assertTrue(
            dispatch_reviewer_request.equivalent_request(
                dispatch_reviewer_request.CLAUDE_REPO, prior, candidate
            )
        )

    def test_deepseek_different_stage_is_not_equivalent(self):
        prior = {
            "pr_number": 466,
            "expected_head": "a" * 40,
            "expected_synthetic": "b" * 40,
            "review_mode": "expert",
        }
        candidate = dict(prior, review_mode="coder")
        self.assertFalse(
            dispatch_reviewer_request.equivalent_request(
                dispatch_reviewer_request.DEEPSEEK_REPO, prior, candidate
            )
        )


class ClaudeReturnTests(unittest.TestCase):
    def test_clean_verdict(self):
        text = "HALLAZGOS: NINGUNO\nVALIDACIÓN OK"
        self.assertEqual(collect_external_reviewer_state.classify_claude_review(text), "CLEAN")

    def test_findings_verdict(self):
        text = "HALLAZGOS: 1\nVALIDACIÓN NO OK"
        self.assertEqual(collect_external_reviewer_state.classify_claude_review(text), "FINDINGS")

    def test_ambiguous_verdict_fails_classification(self):
        text = "VALIDACIÓN OK\nVALIDACIÓN NO OK"
        self.assertEqual(collect_external_reviewer_state.classify_claude_review(text), "FINDINGS")


if __name__ == "__main__":
    unittest.main()
