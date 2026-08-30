from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import select_architect_recovery_mode as routing


class ArchitectRecoveryRoutingTests(unittest.TestCase):
    def event(self, *, added=None, modified=None, removed=None, ref="refs/heads/main"):
        return {
            "ref": ref,
            "head_commit": {
                "added": [] if added is None else added,
                "modified": [] if modified is None else modified,
                "removed": [] if removed is None else removed,
            },
        }

    def test_added_post_spend_routes_only_post_spend(self):
        self.assertEqual(
            routing.select_mode(self.event(added=[routing.POST_SPEND_PATH])),
            "post_spend",
        )

    def test_modified_post_spend_routes_only_post_spend(self):
        self.assertEqual(
            routing.select_mode(self.event(modified=[routing.POST_SPEND_PATH])),
            "post_spend",
        )

    def test_pre_spend_remains_supported(self):
        self.assertEqual(
            routing.select_mode(self.event(modified=[routing.PRE_SPEND_PATH])),
            "pre_spend",
        )

    def test_duplicate_same_path_across_event_arrays_deduplicates(self):
        event = self.event(
            added=[routing.POST_SPEND_PATH],
            modified=[routing.POST_SPEND_PATH],
        )
        self.assertEqual(routing.select_mode(event), "post_spend")

    def test_multiple_paths_fail_closed(self):
        with self.assertRaises(routing.RoutingError):
            routing.select_mode(
                self.event(
                    modified=[routing.POST_SPEND_PATH, "docs/also-changed.md"],
                )
            )

    def test_unknown_single_path_fails_closed(self):
        with self.assertRaises(routing.RoutingError):
            routing.select_mode(self.event(modified=["recovery/unknown.json"]))

    def test_non_main_push_fails_closed(self):
        with self.assertRaises(routing.RoutingError):
            routing.select_mode(
                self.event(modified=[routing.POST_SPEND_PATH], ref="refs/heads/other")
            )

    def test_missing_or_invalid_head_commit_arrays_fail_closed(self):
        with self.assertRaises(routing.RoutingError):
            routing.select_mode({"ref": "refs/heads/main", "head_commit": {}})
        with self.assertRaises(routing.RoutingError):
            routing.select_mode(
                {
                    "ref": "refs/heads/main",
                    "head_commit": {
                        "added": [],
                        "modified": "not-a-list",
                        "removed": [],
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
