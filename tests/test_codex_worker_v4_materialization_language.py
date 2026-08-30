from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_codex_engineer_worker_v2 as v2
import run_codex_engineer_worker_v4 as v4


class CodexWorkerV4MaterializationLanguageTests(unittest.TestCase):
    def test_live_materialize_exact_contract_is_recognized(self) -> None:
        source = "a" * 40
        reference = "b" * 40
        contract = {
            "objective": (
                f"From exact main {source}, materialize exact PR #466 HEAD {reference} "
                "as the sole allowlisted descendant prerequisite, then produce a replacement Draft candidate."
            )
        }
        self.assertEqual(v4.required_materialized_reference(contract, source), reference)

    def test_live_corrected_descendant_contract_is_recognized(self) -> None:
        source = "a" * 40
        reference = "b" * 40
        contract = {
            "objective": (
                f"Produce a corrected descendant of PR #466 HEAD {reference} that closes both "
                "accepted Expert witnesses while preserving the cumulative candidate."
            )
        }
        self.assertEqual(v4.required_materialized_reference(contract, source), reference)

    def test_read_only_reference_still_does_not_materialize(self) -> None:
        source = "a" * 40
        reference = "b" * 40
        contract = {"objective": f"Compare exact historical head {reference} read-only."}
        self.assertIsNone(v4.required_materialized_reference(contract, source))

    def test_explicit_materialization_with_multiple_references_fails_closed(self) -> None:
        source = "a" * 40
        contract = {
            "objective": (
                "Produce a corrected descendant of exact candidates "
                + ("b" * 40)
                + " and "
                + ("c" * 40)
            )
        }
        with self.assertRaisesRegex(v2.WorkerError, "exactly one"):
            v4.required_materialized_reference(contract, source)


if __name__ == "__main__":
    unittest.main()
