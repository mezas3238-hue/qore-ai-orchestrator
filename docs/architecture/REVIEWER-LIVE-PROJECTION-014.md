# REVIEWER-LIVE-PROJECTION-014

## Trigger

The first paid Autonomous V2 end-to-end proof, run `33326713569`, correctly stopped fail-closed after one bounded reconstruction because Sol could not prove that the DeepSeek R94 context correction was present on the reviewer default branch. The external reviewer snapshot exposed current request, open issues/PRs and recent Actions, but omitted exact reviewer `main` identity and a live technical projection.

The failed architect run produced no Codex or reviewer dispatch. Its final status remained `RECONSTRUCTION_REQUIRED`, so the reconstruction loop guard stopped the cycle rather than guessing.

## Correction

`augment_reviewer_control_plane.py` now binds each reviewer control-plane snapshot to the exact live `main` commit and tree, including parent and GitHub signature status. It also carries bounded recent closed PR/issue evidence so completed reviewer-infrastructure corrections are visible to Sol rather than appearing merely as the absence of an open item.

For DeepSeek, the collector constructs a deterministic technical projection from files fetched at the exact reviewer `main` SHA. It fails closed if required contract markers are absent.

The projection distinguishes the live implementation from historical assumptions:

- authoritative model in the review workflow: `deepseek-v4-pro`;
- operational default profile selected by `run_review_with_meter.py`: `compact-budgeted`;
- operational default entrypoint: `scripts/deepseek_reviewer_compact_budgeted_v20.py`;
- explicit stable fallback: `scripts/deepseek_reviewer_v2_1_1_entrypoint.py`;
- stable V2.1 analysis contract retains a high-reasoning analysis and same-model non-thinking verdict extractor, forbids Flash substitution and CoT continuation, and blocks the legacy full-evidence fallback;
- the stable review lane remains three named workflows: auto-dispatch, connection test and QORE review. Other infrastructure/probe/callback workflows are not silently reclassified as members of that review lane.

The exact live QORE review workflow is additionally checked for package-bound run name, BASE/HEAD/SYNTHETIC binding, synthetic parent/tree validation and pre-publication freeze revalidation. Auto-dispatch is checked for separated review/benchmark request files, ambiguous-push refusal and package-contract validation.

## R94 correction evidence

This collector does not hard-code R94 as semantic truth. Instead it now exposes the repository facts Sol requested:

- recent merged reviewer PRs, including the compact exact-QG correction when still within the bounded window;
- recent closed reviewer issues, including its completed incident tracker;
- a larger bounded Actions window so the associated free probe can be correlated;
- exact current main parentage, allowing the correction merge to be proven as an ancestor of the currently live reviewer main;
- exact blob identities for the current QG evidence implementation, which requires full raw QG parsing internally while transporting at most 8,000 characters of authenticated summary/checkout evidence to model context.

## Safety

This change does not weaken the reconstruction guard. It supplies evidence that the guard previously required but could not obtain.

It does not modify qore-core, provider API keys, reviewer publication authority, Production authority or real-capital authority. Reviewer workflow success remains mechanical evidence only; Sol must still independently adjudicate review semantics.
