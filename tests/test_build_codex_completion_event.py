from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_codex_completion_event as completion
from compact_packets_v2 import WorkUnitIdentity


class BuildCodexCompletionEventTests(unittest.TestCase):
    MAIN = "5a158ef0fb2e21db95f2be0685373780bf1ab197"
    CONTRACT = "QORE-UMI14-CORR-UMI13-DS33333453066-FIX-CODEX-015"

    def work_unit(self) -> WorkUnitIdentity:
        return WorkUnitIdentity(
            repository="mezas3238-hue/qore-core",
            source_main_sha=self.MAIN,
            source_tree_sha="a" * 40,
            contract_id=self.CONTRACT,
        )

    def worker_result(self):
        return {
            "changed_files": [
                "docs/architecture/QORE-UMI-13-RECURSIVE-REGISTRY-REVALIDATION-001.md",
                "src/qore/infrastructure/instrument_universe_registry.py",
                "tests/infrastructure/test_instrument_universe_registry_recursive_revalidation.py",
            ],
            "contract_id": self.CONTRACT,
            "diff_sha256": "a073e13bbf6a03f819614976a402f56706ca4a64598261fed25d1e5169d1618e",
            "notes": ["bounded change applied"],
            "production_authority": False,
            "quality_gate_runs": 0,
            "quality_gate_success": False,
            "schema_version": "qore.codex.worker.result.v1",
            "source_main_sha": self.MAIN,
            "status": "BLOCKED",
            "summary": "Codex worker reached the spend-equivalent API token budget after the latest bounded tool action.",
            "turns": 16,
        }

    def usage(self):
        return {
            "model": "gpt-5.3-codex",
            "input_tokens": 361449,
            "cached_tokens": 312704,
            "cache_write_tokens": 0,
            "output_tokens": 5829,
            "budget_tokens": 126648,
            "max_total_tokens": 120000,
            "max_turns": 16,
            "materialized_reference_sha": "df934e5585f59dd0aef17f9ece108d6f39204470",
        }

    def test_real_blocked_after_patch_shape_becomes_compact_hash_bound_event(self):
        event = completion.build_codex_completion_event(
            work_unit=self.work_unit(),
            worker_result=self.worker_result(),
            worker_usage=self.usage(),
        )
        self.assertEqual(event["work_unit_id"], self.work_unit().work_unit_id)
        self.assertEqual(event["status"], "BLOCKED")
        self.assertEqual(event["quality_gate_runs"], 0)
        self.assertFalse(event["quality_gate_success"])
        self.assertEqual(len(event["changed_files"]), 3)
        self.assertEqual(event["usage_summary"]["input_tokens"], 361449)
        self.assertEqual(
            event["usage_summary"]["materialized_reference_sha"],
            "df934e5585f59dd0aef17f9ece108d6f39204470",
        )
        self.assertTrue(event["event_id"].startswith("QORE-CODEX-EVENT-"))
        self.assertEqual(len(event["event_sha256"]), 64)
        self.assertFalse(event["candidate_published"])
        self.assertFalse(event["production_authority"])

    def test_source_main_mismatch_fails_closed(self):
        result = self.worker_result()
        result["source_main_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "source_main_sha"):
            completion.build_codex_completion_event(
                work_unit=self.work_unit(), worker_result=result
            )

    def test_contract_mismatch_fails_closed(self):
        result = self.worker_result()
        result["contract_id"] = "WRONG"
        with self.assertRaisesRegex(ValueError, "contract_id"):
            completion.build_codex_completion_event(
                work_unit=self.work_unit(), worker_result=result
            )

    def test_production_authority_is_rejected(self):
        result = self.worker_result()
        result["production_authority"] = True
        with self.assertRaisesRegex(ValueError, "Production"):
            completion.build_codex_completion_event(
                work_unit=self.work_unit(), worker_result=result
            )

    def test_ready_result_must_have_exact_boolean_qg_status(self):
        result = self.worker_result()
        result["status"] = "READY"
        result["quality_gate_runs"] = 1
        result["quality_gate_success"] = True
        event = completion.build_codex_completion_event(
            work_unit=self.work_unit(), worker_result=result
        )
        self.assertEqual(event["status"], "READY")
        self.assertTrue(event["quality_gate_success"])

        result["quality_gate_success"] = 1
        with self.assertRaisesRegex(ValueError, "exact bool"):
            completion.build_codex_completion_event(
                work_unit=self.work_unit(), worker_result=result
            )


if __name__ == "__main__":
    unittest.main()
