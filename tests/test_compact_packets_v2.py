from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import compact_packets_v2 as packets
import economic_control_plane as eco


class CompactPacketsV2Tests(unittest.TestCase):
    def work_unit(self):
        return packets.WorkUnitIdentity(
            repository="mezas3238-hue/qore-core",
            source_main_sha="a" * 40,
            source_tree_sha="b" * 40,
            contract_id="ENG-1",
        )

    def candidate(self):
        return eco.CandidateIdentity(
            repository="mezas3238-hue/qore-core",
            base_sha="a" * 40,
            head_sha="c" * 40,
            tree_sha="d" * 40,
            synthetic_sha="e" * 40,
        )

    def risk(self):
        return eco.classify_risk_shadow(["src/qore/contracts/example.py"], semantic_change=True)

    def sol_kwargs(self):
        return {
            "risk": self.risk(),
            "workflow_state": "ENGINEERING_REQUIRED",
            "last_event": "ARCHITECT_RECONSTRUCTED",
            "decision_required": "define bounded engineering task",
            "active_contract": {"id": "ENG-1"},
            "semantic_questions": ["preserve invariant"],
            "changed_files": [],
            "diff_summary": {},
            "findings": {"open": [], "resolved": []},
            "qg_summary": {},
            "review_summary": {},
            "source_slices": [],
            "budget_remaining_usd": 5.0,
            "allowed_transitions": ["CODEX"],
        }

    def test_pre_candidate_sol_packet_binds_work_unit_not_fake_candidate(self):
        packet = packets.build_sol_decision_packet_v2(
            subject_kind=packets.SolSubjectKind.WORK_UNIT,
            work_unit=self.work_unit(),
            candidate=None,
            **self.sol_kwargs(),
        )
        self.assertEqual(packet["subject_kind"], "WORK_UNIT")
        self.assertTrue(packet["subject_id"].startswith("QORE-WORK-"))
        self.assertNotIn("candidate_id", packet)
        self.assertFalse(packet["production_authority"])

    def test_frozen_candidate_sol_packet_uses_candidate_identity(self):
        packet = packets.build_sol_decision_packet_v2(
            subject_kind=packets.SolSubjectKind.CANDIDATE,
            work_unit=None,
            candidate=self.candidate(),
            **self.sol_kwargs(),
        )
        self.assertEqual(packet["subject_kind"], "CANDIDATE")
        self.assertEqual(packet["subject_id"], self.candidate().candidate_id)

    def test_subject_lifecycle_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "WORK_UNIT"):
            packets.build_sol_decision_packet_v2(
                subject_kind=packets.SolSubjectKind.WORK_UNIT,
                work_unit=self.work_unit(),
                candidate=self.candidate(),
                **self.sol_kwargs(),
            )

    def test_codex_capsule_does_not_require_future_candidate(self):
        capsule = packets.build_codex_task_capsule_v2(
            work_unit=self.work_unit(),
            reference_sha="f" * 40,
            prior_candidate=None,
            changed_file_allowlist=["src/qore/example.py"],
            forbidden_files=["src/qore/production.py"],
            contract={"contract_id": "ENG-1"},
            findings=[],
            acceptance_tests=["python3 -m unittest"],
            source_slices=[{"symbol": "A"}],
            relevant_tests=["tests/test_example.py"],
            historical_delta={"materialized": True},
        )
        self.assertEqual(capsule["work_unit_id"], self.work_unit().work_unit_id)
        self.assertIsNone(capsule["prior_candidate"])
        self.assertIn("Do not rediscover", capsule["worker_instruction"])
        self.assertFalse(capsule["production_authority"])

    def test_correction_capsule_may_bind_prior_candidate_with_same_base(self):
        capsule = packets.build_codex_task_capsule_v2(
            work_unit=self.work_unit(),
            reference_sha=None,
            prior_candidate=self.candidate(),
            changed_file_allowlist=["src/qore/example.py"],
            forbidden_files=[],
            contract={"contract_id": "ENG-1"},
            findings=[{"id": "F1"}],
            acceptance_tests=[],
            source_slices=[],
            relevant_tests=[],
            historical_delta={},
        )
        self.assertEqual(capsule["prior_candidate"]["head_sha"], "c" * 40)


if __name__ == "__main__":
    unittest.main()
