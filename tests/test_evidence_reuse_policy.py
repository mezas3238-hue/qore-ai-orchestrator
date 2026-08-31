from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import economic_control_plane as eco
import evidence_reuse_policy as reuse


class EvidenceReusePolicyTests(unittest.TestCase):
    def candidate(self, head: str, tree: str, synthetic: str):
        return eco.CandidateIdentity(
            repository="mezas3238-hue/qore-core",
            base_sha="a" * 40,
            head_sha=head * 40,
            tree_sha=tree * 40,
            synthetic_sha=synthetic * 40,
        )

    def reuse_input(self, evidence_type: str, old, new, **overrides):
        values = {
            "evidence_type": evidence_type,
            "old_candidate": old,
            "new_candidate": new,
            "old_input_digest": "same-input",
            "new_input_digest": "same-input",
            "old_contract_version": "v1",
            "new_contract_version": "v1",
            "relevant_blob_digest_unchanged": True,
            "exact_materialization_inputs_unchanged": True,
        }
        values.update(overrides)
        return reuse.EvidenceReuseInput(**values)

    def test_new_head_invalidates_semantic_reviews_even_if_other_inputs_same(self):
        old = self.candidate("b", "c", "d")
        new = self.candidate("e", "f", "1")
        result = reuse.decide_evidence_reuse(
            self.reuse_input("REVIEW_EXPERT", old, new)
        )
        self.assertEqual(
            result.decision, reuse.EvidenceReuseDecision.INVALIDATE_SEMANTIC_AUTHORITY
        )
        self.assertFalse(result.semantic_authority_reused)

    def test_qg_recomputes_on_head_change(self):
        old = self.candidate("b", "c", "d")
        new = self.candidate("e", "f", "1")
        result = reuse.decide_evidence_reuse(
            self.reuse_input("QG_EVIDENCE", old, new)
        )
        self.assertEqual(result.decision, reuse.EvidenceReuseDecision.RECOMPUTE)

    def test_source_slice_can_reuse_unchanged_blob_fact_without_authority(self):
        old = self.candidate("b", "c", "d")
        new = self.candidate("e", "f", "1")
        result = reuse.decide_evidence_reuse(
            self.reuse_input("SOURCE_SLICE", old, new)
        )
        self.assertEqual(result.decision, reuse.EvidenceReuseDecision.REUSE_FACT_ONLY)
        self.assertFalse(result.semantic_authority_reused)

    def test_contract_change_invalidates_semantic_review_on_same_candidate(self):
        candidate = self.candidate("b", "c", "d")
        result = reuse.decide_evidence_reuse(
            self.reuse_input(
                "REVIEW_CLAUDE",
                candidate,
                candidate,
                new_contract_version="v2",
            )
        )
        self.assertEqual(
            result.decision, reuse.EvidenceReuseDecision.INVALIDATE_SEMANTIC_AUTHORITY
        )

    def test_unknown_evidence_class_recomputes(self):
        candidate = self.candidate("b", "c", "d")
        result = reuse.decide_evidence_reuse(
            self.reuse_input("UNKNOWN", candidate, candidate)
        )
        self.assertEqual(result.decision, reuse.EvidenceReuseDecision.RECOMPUTE)


if __name__ == "__main__":
    unittest.main()
