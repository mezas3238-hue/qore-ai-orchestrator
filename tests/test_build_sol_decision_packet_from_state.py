from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_sol_decision_packet_from_state as target


class SolPacketFromStateTests(unittest.TestCase):
    def subject(self):
        return {
            "schema_version": "qore.work.unit.shadow.v1",
            "work_unit": {
                "repository": "mezas3238-hue/qore-core",
                "source_main_sha": "a" * 40,
                "source_tree_sha": "b" * 40,
                "contract_id": "ENG-1",
                "production_authority": False,
            },
        }

    def state(self):
        return {
            "changed_files": ["src/qore/contracts/a.py"],
            "semantic_change": True,
            "release_or_production_sensitive": False,
            "workflow_state": "ENGINEERING_BLOCKED",
            "last_event": "CODEX_BLOCKED",
            "decision_required": "Adjudicate one exact semantic question.",
            "active_contract": {"contract_id": "ENG-1"},
            "semantic_questions": ["valid?"],
            "diff_summary": {"sha256": "c" * 64},
            "findings": {},
            "qg_summary": {},
            "review_summary": {},
            "source_slices": [],
            "budget_remaining_usd": 4.0,
            "allowed_transitions": ["ENGINEERING_TASK"],
            "production_authority": False,
        }

    def test_work_unit_packet_is_hash_bound_and_semantic_tier(self):
        packet = target.build_packet(self.subject(), self.state())
        self.assertEqual(packet["schema_version"], "qore.sol.decision.packet.v2")
        self.assertEqual(packet["subject_kind"], "WORK_UNIT")
        self.assertEqual(packet["risk_tier"], 2)
        self.assertTrue(packet["packet_id"].startswith("QORE-SOL-PKT2-"))
        self.assertFalse(packet["production_authority"])

    def test_candidate_binding_is_supported(self):
        subject = {
            "schema_version": "qore.candidate.binding.api.v1",
            "repository": "mezas3238-hue/qore-core",
            "base_sha": "1" * 40,
            "head_sha": "2" * 40,
            "tree_sha": "3" * 40,
            "synthetic_sha": "4" * 40,
        }
        packet = target.build_packet(subject, self.state())
        self.assertEqual(packet["subject_kind"], "CANDIDATE")
        self.assertEqual(packet["subject"]["head_sha"], "2" * 40)

    def test_production_authority_fails_closed(self):
        state = self.state()
        state["production_authority"] = True
        with self.assertRaisesRegex(ValueError, "Production"):
            target.build_packet(self.subject(), state)


if __name__ == "__main__":
    unittest.main()
