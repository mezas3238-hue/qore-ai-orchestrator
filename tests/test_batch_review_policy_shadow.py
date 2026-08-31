from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import batch_review_policy_shadow as batch
import economic_control_plane as eco


class BatchReviewPolicyShadowTests(unittest.TestCase):
    def candidate(self, char: str) -> eco.CandidateIdentity:
        return eco.CandidateIdentity(
            repository="mezas3238-hue/qore-core",
            base_sha="a" * 40,
            head_sha=char * 40,
            tree_sha=("c" if char != "c" else "d") * 40,
            synthetic_sha=("e" if char != "e" else "f") * 40,
        )

    def test_independent_nonsemantic_tier0_tier1_can_be_shadow_eligible(self):
        leaves = [
            batch.BatchLeaf(self.candidate("b"), eco.RiskTier.T0, "freeze-1", False),
            batch.BatchLeaf(self.candidate("f"), eco.RiskTier.T1, "freeze-2", False),
        ]
        result = batch.evaluate_batch_shadow(leaves)
        self.assertTrue(result.eligible)
        self.assertTrue(result.shadow_only)
        self.assertEqual(len(result.independent_leaf_ids), 2)

    def test_semantic_or_tier2_plus_never_batches(self):
        semantic = [
            batch.BatchLeaf(self.candidate("b"), eco.RiskTier.T1, "freeze-1", True),
            batch.BatchLeaf(self.candidate("f"), eco.RiskTier.T1, "freeze-2", False),
        ]
        high = [
            batch.BatchLeaf(self.candidate("b"), eco.RiskTier.T2, "freeze-1", False),
            batch.BatchLeaf(self.candidate("f"), eco.RiskTier.T1, "freeze-2", False),
        ]
        self.assertFalse(batch.evaluate_batch_shadow(semantic).eligible)
        self.assertFalse(batch.evaluate_batch_shadow(high).eligible)

    def test_candidate_change_invalidates_only_exact_leaf(self):
        leaves = [
            batch.BatchLeaf(self.candidate("b"), eco.RiskTier.T0, "freeze-1", False),
            batch.BatchLeaf(self.candidate("f"), eco.RiskTier.T1, "freeze-2", False),
        ]
        invalidated = batch.invalidate_leaf_after_candidate_change(
            leaves, changed_candidate_id=leaves[0].candidate.candidate_id
        )
        self.assertEqual(invalidated, (leaves[0].candidate.candidate_id,))


if __name__ == "__main__":
    unittest.main()
