from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_candidate_binding_api as target


class CandidateBindingApiTests(unittest.TestCase):
    BASE = "a" * 40
    HEAD = "b" * 40
    SYNTHETIC = "c" * 40
    TREE = "d" * 40

    def payload(self, path: str):
        if path == "/pulls/9":
            return {
                "state": "open",
                "merged": False,
                "draft": True,
                "base": {"sha": self.BASE},
                "head": {"sha": self.HEAD},
                "merge_commit_sha": self.SYNTHETIC,
            }
        if path == f"/git/commits/{self.HEAD}":
            return {"tree": {"sha": self.TREE}, "parents": [{"sha": self.BASE}]}
        if path == f"/git/commits/{self.SYNTHETIC}":
            return {
                "tree": {"sha": self.TREE},
                "parents": [{"sha": self.BASE}, {"sha": self.HEAD}],
            }
        if path == f"/compare/{self.BASE}...{self.HEAD}":
            return {"status": "ahead"}
        raise AssertionError(path)

    def fake_api(self, token: str, repository: str, path: str):
        self.assertEqual(repository, "owner/repo")
        return self.payload(path)

    @patch.object(target, "api_json")
    def test_exact_binding(self, api):
        api.side_effect = self.fake_api
        result = target.build_candidate_binding_api(
            token="token",
            repository="owner/repo",
            pr_number=9,
            expected_base=self.BASE,
            expected_head=self.HEAD,
            expected_synthetic=self.SYNTHETIC,
        )
        self.assertEqual(result["base_sha"], self.BASE)
        self.assertEqual(result["head_sha"], self.HEAD)
        self.assertEqual(result["tree_sha"], self.TREE)
        self.assertEqual(result["synthetic_parents"], [self.BASE, self.HEAD])
        self.assertFalse(result["production_authority"])

    @patch.object(target, "api_json")
    def test_moved_head_fails_closed(self, api):
        api.side_effect = self.fake_api
        with self.assertRaisesRegex(ValueError, "HEAD no longer matches"):
            target.build_candidate_binding_api(
                token="token",
                repository="owner/repo",
                pr_number=9,
                expected_head="e" * 40,
            )

    @patch.object(target, "api_json")
    def test_synthetic_parent_mismatch_fails_closed(self, api):
        def bad(token: str, repository: str, path: str):
            value = self.payload(path)
            if path == f"/git/commits/{self.SYNTHETIC}":
                value = dict(value)
                value["parents"] = [{"sha": self.HEAD}, {"sha": self.BASE}]
            return value

        api.side_effect = bad
        with self.assertRaisesRegex(ValueError, "parents"):
            target.build_candidate_binding_api(
                token="token",
                repository="owner/repo",
                pr_number=9,
            )

    @patch.object(target, "api_json")
    def test_non_ancestor_fails_closed(self, api):
        def bad(token: str, repository: str, path: str):
            value = self.payload(path)
            if path.startswith("/compare/"):
                return {"status": "diverged"}
            return value

        api.side_effect = bad
        with self.assertRaisesRegex(ValueError, "not an ancestor"):
            target.build_candidate_binding_api(
                token="token",
                repository="owner/repo",
                pr_number=9,
            )


if __name__ == "__main__":
    unittest.main()
