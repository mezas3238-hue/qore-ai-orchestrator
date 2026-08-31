from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_sol_architect_v3 as target


class SolArchitectV3Tests(unittest.TestCase):
    def packet(self):
        return {
            "schema_version": "qore.sol.decision.packet.v2",
            "subject_kind": "WORK_UNIT",
            "subject_id": "QORE-WORK-123",
            "subject": {
                "repository": "mezas3238-hue/qore-core",
                "source_main_sha": "a" * 40,
                "source_tree_sha": "b" * 40,
                "contract_id": "ENG-1",
                "production_authority": False,
            },
            "risk_tier": 2,
            "risk_reasons": ["explicit_semantic_change"],
            "workflow_state": "ENGINEERING_BLOCKED",
            "last_event": "CODEX_BLOCKED",
            "decision_required": "Adjudicate whether the bounded diff is valid.",
            "active_contract": {"contract_id": "ENG-1"},
            "open_semantic_questions": ["is the correction semantically valid?"],
            "changed_files": ["src/qore/a.py"],
            "diff_summary": {"sha256": "c" * 64},
            "findings": {},
            "qg_summary": {},
            "review_summary": {},
            "source_slices": [],
            "budget_remaining_usd": 4.0,
            "allowed_transitions": ["ENGINEERING_TASK", "REVIEW_TASK"],
            "production_authority": False,
            "packet_sha256": "d" * 64,
            "packet_id": "QORE-SOL-PKT2-123",
        }

    def prefix(self):
        return {
            "schema_version": "qore.stable.prompt.prefix.v1",
            "role": "SOL",
            "contract_version": "v1",
            "manifest": [],
            "prefix_text": "stable invariant corpus",
            "prefix_sha256": "e" * 64,
            "prefix_chars": 23,
            "prompt_cache_key": "qore-sol-v1-eeee",
            "mutation_policy": "APPEND_DYNAMIC_CONTEXT_AFTER_STABLE_PREFIX_ONLY",
            "production_authority": False,
        }

    def test_work_unit_source_main_is_bound(self):
        kind, source, subject_id = target.validate_packet(self.packet())
        self.assertEqual(kind, "WORK_UNIT")
        self.assertEqual(source, "a" * 40)
        self.assertEqual(subject_id, "QORE-WORK-123")

    def test_candidate_uses_base_as_architect_source_main(self):
        packet = self.packet()
        packet["subject_kind"] = "CANDIDATE"
        packet["subject"] = {
            "repository": "mezas3238-hue/qore-core",
            "base_sha": "1" * 40,
            "head_sha": "2" * 40,
            "tree_sha": "3" * 40,
            "synthetic_sha": "4" * 40,
            "production_authority": False,
        }
        _, source, _ = target.validate_packet(packet)
        self.assertEqual(source, "1" * 40)

    def test_stable_prefix_is_first_cache_breakpoint(self):
        model_input = target._model_input(
            stable_text="STABLE",
            packet=self.packet(),
            effort="high",
        )
        content = model_input[0]["content"]
        self.assertEqual(content[0]["text"], "STABLE")
        self.assertEqual(content[0]["prompt_cache_breakpoint"], {"mode": "explicit"})
        self.assertIn("QORE_SOL_DECISION_PACKET_V2", content[1]["text"])

    def test_production_authority_fails_closed(self):
        packet = self.packet()
        packet["production_authority"] = True
        with self.assertRaisesRegex(ValueError, "Production"):
            target.validate_packet(packet)

    def test_empty_decision_required_fails_closed(self):
        packet = self.packet()
        packet["decision_required"] = ""
        with self.assertRaisesRegex(ValueError, "decision_required"):
            target.validate_packet(packet)

    def test_usage_records_packet_and_prefix_identity(self):
        usage = target.safe_usage_record(
            {
                "id": "resp",
                "model": "gpt-5.6-sol",
                "status": "completed",
                "usage": {
                    "input_tokens": 10,
                    "input_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 0},
                    "output_tokens": 2,
                    "output_tokens_details": {"reasoning_tokens": 1},
                    "total_tokens": 12,
                },
            },
            effort="high",
            packet=self.packet(),
            prefix=self.prefix(),
            max_output_tokens=8000,
        )
        self.assertEqual(usage["packet_id"], "QORE-SOL-PKT2-123")
        self.assertEqual(usage["stable_prefix_sha256"], "e" * 64)
        self.assertEqual(usage["model_calls"], 1)
        self.assertFalse(usage["production_authority"])


if __name__ == "__main__":
    unittest.main()
