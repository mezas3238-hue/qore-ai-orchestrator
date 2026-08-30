# CODEX WORKER V4 — DETERMINISTIC HISTORICAL REFERENCE MATERIALIZATION

## Reproduced residual defect

Real V3 run `33338345894` proved that cache stability and historical read access were fixed: it cached 258,432 of 301,119 input tokens, consumed only 80,627 input-equivalent budget units, allowlisted exact PR #466 HEAD `df934e5585f59dd0aef17f9ece108d6f39204470`, and invoked `reference_diff` on turn 2.

It nevertheless exhausted all 16 turns without a patch. The active architect objective explicitly required the cumulative replacement candidate to start by fetching/checking out that exact historical HEAD. A read-only historical diff does not satisfy that starting-state contract; Codex was still forced to reconstruct the cumulative candidate manually.

## V4 controller operation

V4 preserves the V3 model loop and all existing model limits. Before any model spend, after V3/V2 request validation has authenticated the exact clean `source_main_sha`, the controller may materialize one historical reference delta **only** when the immutable objective explicitly contains a starting/check-out/cumulative-replacement instruction.

Materialization fails closed unless:

- the objective explicitly requests historical starting-state materialization;
- exactly one historical 40-hex SHA is present in the contract allowlist;
- local HEAD still equals exact `source_main_sha`;
- the source working tree is clean;
- the historical commit exists in the already-cloned qore-core repository;
- `git merge-base(source_main_sha, reference_sha) == source_main_sha`;
- the binary source→reference patch has safe repository-relative paths, no symlink/submodule or file-mode changes, and remains within the existing 30-file/120k-patch bounds;
- `git apply --check --binary --whitespace=error-all` succeeds;
- after application, the exact changed-file set matches the historical delta;
- every materialized regular file hashes to the exact blob in the historical reference, and historical deletions are absent locally.

The repository remains detached at `source_main_sha`; the historical candidate exists only as the working-tree delta. This allows the existing candidate fingerprint, full QG and isolated publication controller to treat it as one cumulative candidate.

## No new model authority

V4 adds no model tool and grants no additional credential. There is still no arbitrary shell, fetch, network tool, checkout, commit, merge, push, reviewer dispatch, provider authority, Production authority or real-capital authority available to Codex.

The model still uses GPT-5.3-Codex, `store=false`, stateless immutable-prefix replay, max 16 turns and max 120,000 input-price-equivalent units. Controller QG and publication boundaries are unchanged.

## Model guidance

When a reference was materialized, V4 appends a controller protocol to the existing Codex charter stating that the exact cumulative historical delta is already present, must not be reconstructed or reapplied, and that Codex should inspect only the smallest relevant helpers/tests, implement the incremental accepted corrections promptly, then run targeted tests and the full QG.

## Evidence

The usage artifact adds only non-secret materialization metadata: policy identifier, materialized SHA, source/merge-base SHA, changed-file names and SHA-256 of the historical patch. It never embeds reference diff contents, model arguments, secrets or file contents.

No statement here grants Production readiness, operational readiness, trading authority or real-capital authorization.
