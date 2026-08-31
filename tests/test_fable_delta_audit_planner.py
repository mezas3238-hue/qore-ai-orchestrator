from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fable_delta_audit_planner as planner


class FableDeltaAuditPlannerTests(unittest.TestCase):
    def test_changed_file_expands_to_dependents_and_keeps_isolated_reference_only(self):
        previous = [
            {"path": "a.py", "sha256": "a" * 64},
            {"path": "b.py", "sha256": "b" * 64},
            {"path": "c.py", "sha256": "c" * 64},
            {"path": "isolated.py", "sha256": "d" * 64},
        ]
        current = {
            "schema_version": "qore.static.code.index.v1",
            "files": [
                {"path": "a.py", "sha256": "e" * 64},
                {"path": "b.py", "sha256": "b" * 64},
                {"path": "c.py", "sha256": "c" * 64},
                {"path": "isolated.py", "sha256": "d" * 64},
            ],
            "local_dependency_edges": [
                {"from": "b.py", "to": "a.py", "import": "a"},
                {"from": "c.py", "to": "b.py", "import": "b"},
            ],
        }
        result = planner.plan_delta_audit(
            previous_manifest=previous,
            current_index=current,
            forced_cross_boundary_paths=[],
        )
        self.assertEqual(result["changed"], ["a.py"])
        self.assertEqual(result["transitively_impacted"], ["b.py", "c.py"])
        self.assertEqual(result["unchanged_isolated"], ["isolated.py"])
        self.assertEqual(result["fresh_audit_paths"], ["a.py", "b.py", "c.py"])
        self.assertFalse(result["production_authority"])

    def test_add_remove_and_forced_boundary_are_explicit(self):
        previous = [
            {"path": "old.py", "sha256": "a" * 64},
            {"path": "boundary.py", "sha256": "b" * 64},
        ]
        current = {
            "schema_version": "qore.static.code.index.v1",
            "files": [
                {"path": "new.py", "sha256": "c" * 64},
                {"path": "boundary.py", "sha256": "b" * 64},
            ],
            "local_dependency_edges": [],
        }
        result = planner.plan_delta_audit(
            previous_manifest=previous,
            current_index=current,
            forced_cross_boundary_paths=["boundary.py"],
        )
        self.assertEqual(result["added"], ["new.py"])
        self.assertEqual(result["removed"], ["old.py"])
        self.assertEqual(result["fresh_audit_paths"], ["boundary.py", "new.py"])
        self.assertIn("periodic full-system recertification is not waived", result["rules"])

    def test_unknown_forced_boundary_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "must exist"):
            planner.plan_delta_audit(
                previous_manifest=[],
                current_index={
                    "schema_version": "qore.static.code.index.v1",
                    "files": [],
                    "local_dependency_edges": [],
                },
                forced_cross_boundary_paths=["missing.py"],
            )


if __name__ == "__main__":
    unittest.main()
