# FROZEN-QG-EVIDENCE-019

## Purpose

Reviewer dispatch must not depend on GitHub's downloadable job-log transport. The exact QG summary already frozen into an earlier reviewer request for the same candidate is durable evidence and can be reused only after live revalidation.

## Acceptance contract

The architect snapshot may supply a QG summary only from `external_reviewer_state.{deepseek,claude}.current_request` when that request matches all four live freeze coordinates exactly:

- PR number;
- BASE;
- HEAD;
- SYNTHETIC.

The QG summary has an exact key set. Ruff must be true; all numeric fields use exact runtime integers; run/job IDs and counts are positive where applicable; pytest collected must equal pytest passed; coverage values must be internally valid. If more than one exact-freeze summary exists, every normalized value must be byte-equivalent or package construction fails closed.

The summary is never trusted by itself. Before reviewer publication the controller re-fetches the referenced qore-core QORE CI run and quality job and requires exact workflow ID/name/path, pull-request event, exact HEAD, completed status and success conclusion. The quality job must match the exact run, HEAD, name, completed status and success conclusion.

`build_reviewer_package_public_logs.py` remains the workflow compatibility entrypoint, but its operational `main()` delegates to `build_reviewer_package_frozen_qg.py`; the legacy public-log helpers are diagnostic only and are not called by package construction.

## Run-4 recovery

Autonomous V2 run `33330339041` froze the R94 DeepSeek request in its immutable `qore-state.json`. That request is bound to PR #466 and the current freeze and contains QG run `33283252638`, quality job `99181893347`, Ruff PASS, Mypy 741 source files, pytest 4887/4887 with 7 warnings and coverage 47615/6235/87%.

Two recovery attempts failed before package generation solely while trying two job-log transports. Because neither attempt created reviewer package/dispatch evidence, one final evidence-method recovery slot is permitted. Three total failed recovery attempts exhaust the cap. Active, successful, side-effecting, ambiguous or unexpected prior recovery state still blocks.

No model call is made by recovery. No Production, real-capital, provider, productive-credential or Risk authority is added.
