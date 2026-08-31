from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_stable_prompt_prefix as prefix


class BuildStablePromptPrefixTests(unittest.TestCase):
    def test_prefix_is_deterministic_and_dynamic_context_is_not_embedded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "charter.md").write_text("ROLE\n", encoding="utf-8")
            (root / "invariants.md").write_text("NO PRODUCTION\n", encoding="utf-8")
            first = prefix.build_stable_prefix(
                root=root,
                role="SOL",
                contract_version="v1",
                files=["charter.md", "invariants.md"],
            )
            second = prefix.build_stable_prefix(
                root=root,
                role="SOL",
                contract_version="v1",
                files=["charter.md", "invariants.md"],
            )
            self.assertEqual(first["prefix_sha256"], second["prefix_sha256"])
            self.assertEqual(first["prompt_cache_key"], second["prompt_cache_key"])
            self.assertEqual(
                first["mutation_policy"],
                "APPEND_DYNAMIC_CONTEXT_AFTER_STABLE_PREFIX_ONLY",
            )
            self.assertFalse(first["production_authority"])

    def test_changed_prefix_source_changes_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "charter.md"
            path.write_text("A\n", encoding="utf-8")
            first = prefix.build_stable_prefix(
                root=root, role="CODEX", contract_version="v1", files=["charter.md"]
            )
            path.write_text("B\n", encoding="utf-8")
            second = prefix.build_stable_prefix(
                root=root, role="CODEX", contract_version="v1", files=["charter.md"]
            )
            self.assertNotEqual(first["prefix_sha256"], second["prefix_sha256"])

    def test_path_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "under root"):
                prefix.build_stable_prefix(
                    root=root,
                    role="SOL",
                    contract_version="v1",
                    files=["../secret.txt"],
                )


if __name__ == "__main__":
    unittest.main()
