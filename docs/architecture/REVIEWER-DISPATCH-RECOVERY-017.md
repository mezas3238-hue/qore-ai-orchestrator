# Reviewer Dispatch Recovery 017

## Purpose

This unit recovers one already-paid, already-valid Sol `REVIEW_TASK` decision when the canonical Autonomous V2 run fails mechanically before it can create or dispatch the reviewer package. It does not ask Sol to reason again and it does not create a new semantic decision.

The motivating failure is Autonomous V2 run `33330339041`: Sol completed its bounded xhigh/max reasoning and selected one DeepSeek Expert review for frozen qore-core PR #466, but `build_reviewer_package.py` could not read the exact QORE CI job log because the package-build step lacked an authorized qore-core Actions-read credential.

## Trust boundary

A recovery source must be the exact canonical workflow:

- workflow name: `QORE Architect autonomous V2`;
- workflow path: `.github/workflows/qore-architect-autonomous-v2.yml`;
- event: `workflow_dispatch`;
- branch: `main`;
- status: `completed`;
- conclusion: `failure`;
- exact observed source HEAD;
- immutable artifact `qore-architect-v2-<source_run_id>`;
- final decision status `REVIEW_TASK`;
- next actor `DEEPSEEK` or `CLAUDE_CODE`;
- enabled review contract;
- decision `source_main_sha` exactly equal to the consistent qore-core snapshot main SHA;
- no `reviewer-package.json` and no `reviewer-dispatch.json` already present in the source artifact.

If any binding is absent or ambiguous, recovery fails closed.

## Two-stage recovery

`QORE reviewer dispatch recovery trigger` observes failed canonical Autonomous V2 completions. It does not itself write to reviewer repositories. It validates the source artifact, deduplicates by exact source run ID, and starts at most one recovery parent workflow.

A one-shot file `recovery/reviewer-dispatch-current.json` may trigger the same validation after infrastructure deployment. This exists so a failure that predates installation of the `workflow_run` listener can be recovered without rerunning Sol. The file is only a lookup request; the controller re-fetches and validates the exact source run and artifact from GitHub.

The recovery parent workflow intentionally has logical workflow name `QORE Architect autonomous V2` while using a distinct file path `.github/workflows/qore-architect-review-recovery-v1.yml`. This preserves the existing callback parent contract in `resume_after_agent_completion.py`. The recovery trigger requires the canonical source *path*, so the recovery child cannot recursively recover itself even though the logical workflow name is the same.

## No model spend

The recovery parent never receives `OPENAI_SOL_API_KEY` and never invokes `run_sol_architect_v2.py`. It copies only bounded, allowlisted JSON evidence from the immutable source artifact, including the observed Sol usage files. Therefore later budget accounting sees the already-spent Sol calls rather than inferring zero cost.

The recovery parent creates a fresh reviewer package whose `R<run_id>` lineage points to the recovery parent run. It uploads `qore-architect-v2-<recovery_run_id>` so the normal reviewer-completion callback can bind the package to an accepted parent artifact and continue through the standard bounded resume controller.

## Exact QG read credential

`build_reviewer_package.py` already separates qore-core read authentication behind `QORE_CORE_READ_TOKEN`. In recovery, only the exact package-build step aliases `QORE_REVIEWER_DISPATCH_TOKEN` into `QORE_CORE_READ_TOKEN`. This gives the builder the already-preflighted qore-core Actions/contents read authority required to authenticate the exact quality job log. The token is not placed in prompts, artifacts, summaries, telemetry, or model context.

The ordinary Autonomous V2 package-build path must use the same narrow alias so future review tasks do not reproduce the HTTP 403 failure.

## Terminal failed equivalent DeepSeek stage

Normal reviewer dispatch continues to reject equivalent `requests/current.json` work. Recovery has one narrower exception, currently only for DeepSeek:

1. the current request is semantically equivalent to the new candidate stage but has a different package ID;
2. exactly one DeepSeek review workflow run is bound to the prior package, using package-bound `run-name` or the exact historical run-HEAD `requests/current.json` for pre-hardening legacy runs;
3. that run is terminal with a retryable non-success conclusion;
4. no exact semantic publication marker exists in qore-core for that prior package and exact HEAD:
   `<!-- QORE-DEEPSEEK-REVIEW package=<package> head=<head> -->`;
5. the new package path is unique.

A successful, active, ambiguous, already-published, same-package, or Claude-equivalent stage is not supersedable and fails closed.

This permits recovery from the known R94 infrastructure failure without reusing or pretending R94 passed. The recovered package remains a distinct new Expert package.

## Callback continuity

After the recovered reviewer completes, the existing cross-repository callback remains authoritative:

`reviewer completion -> repository_dispatch -> QORE agent completion resume -> exact parent artifact binding -> budget/loop gates -> fresh Autonomous V2`

GitHub workflow conclusion is evidence only. Reviewer semantic PASS/FINDINGS remain subject to Sol adjudication in the resumed architect cycle.

## Permanent prohibitions

Recovery grants no Production authority. It cannot activate productive credentials, real-money execution, deposits/withdrawals, real capital, or Risk bypass. It does not change qore-core and cannot infer Production readiness from semantic or TEST/DEMO evidence.
