from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_codex_engineer_worker_v6 as v6
import run_codex_engineer_worker_v6_hardened as target


class CodexWorkerV6HardenedTests(unittest.TestCase):
    @patch.object(v6, "execute_v6")
    def test_wrapper_temporarily_installs_hardened_scope_policy(self, execute):
        original = v6._initial_evidence

        def fake_execute(**kwargs):
            self.assertIs(v6._initial_evidence, target.scope_policy.hardened_initial_evidence)
            return (
                {"status": "BLOCKED", "changed_files": [], "production_authority": False},
                {"model_calls": 1, "production_authority": False},
            )

        execute.side_effect = fake_execute
        final, usage = target.execute_hardened(
            key="x", repo=Path("."), request={}, charter="charter"
        )
        self.assertIs(v6._initial_evidence, original)
        self.assertEqual(usage["worker_version"], "v6-hardened")
        self.assertEqual(usage["scope_policy"], "objective_scope_write__acceptance_tests_read_only_v1")
        self.assertFalse(final["production_authority"])

    def test_activation_modes_are_explicitly_bounded(self):
        self.assertEqual(target.ALLOWED_ACTIVATION_MODES, {"CONTROLLED_VALIDATION", "LIMITED_LIVE"})


if __name__ == "__main__":
    unittest.main()
