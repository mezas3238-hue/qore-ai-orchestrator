# QORE Deterministic-First AI Cost Control Plane — 058

## Status

Implementation foundation. All routing and Fable policy introduced here is **shadow-only**. This change does not dispatch Sol, Codex, DeepSeek, Claude, or Fable and does not modify qore-core.

## Objective

Reduce AI operating cost by removing deterministic work from model calls while preserving or increasing engineering assurance.

Normative flow:

```text
GITHUB EVENT
  -> DETERMINISTIC STATE REDUCER
  -> SEMANTIC DELTA GATE
      -> NO AI REQUIRED: deterministic next state
      -> AI REQUIRED: minimal immutable hash-bound package
  -> ONE HIGH-VALUE MODEL CALL
  -> DETERMINISTIC VERIFICATION
  -> EVIDENCE LEDGER
  -> NEXT AI ONLY IF NEW SEMANTIC JUDGMENT IS REQUIRED
```

Normative rule: **an AI call is not eligible unless the controller can state the exact semantic uncertainty or engineering judgment that requires the model.**

## Permanent invariants

- GitHub remains the sole source of truth.
- qore-core remains provider-neutral; this work lives only in orchestration infrastructure.
- No Production authority is introduced. Every new package and report fixes `production_authority=false`.
- Existing bounded-autonomy caps and recovery paths are not bypassed.
- No reviewer reduction is activated by this change.
- For every risk tier in this migration stage, the preauthorized review plan remains:
  `QG -> DEEPSEEK_EXPERT -> DEEPSEEK_CODER -> CLAUDE -> SOL_FINAL`.
- A clean reviewer callback is only a wake-up signal. Exact candidate, evidence completeness, and anomaly checks must remain true before deterministic stage advancement is even eligible.
- A new HEAD/freeze invalidates semantic reviews under the existing freeze discipline.

## Candidate identity

`CandidateIdentity` binds:

- repository
- BASE
- HEAD
- TREE
- SYNTHETIC
- `production_authority=false`

The canonical JSON representation yields a deterministic `QORE-CAND-*` identity.

## Evidence Ledger

`EvidenceLedger` is an append-only deterministic index keyed by an immutable evidence digest. Evidence includes:

- candidate identity
- evidence type
- input digest
- tool version
- command
- output digest
- explicit same-candidate reuse eligibility

The ledger records facts; it never grants semantic approval.

Expected evidence classes include:

- `QG_EVIDENCE`
- `MATERIALIZATION_EVIDENCE`
- `TARGETED_TEST_EVIDENCE`
- `DIFF_EVIDENCE`
- `SOURCE_SLICE`
- `REVIEW_EXPERT`
- `REVIEW_CODER`
- `REVIEW_CLAUDE`
- `SOL_ADJUDICATION`
- `FABLE_AUDIT`

## Unified Cost Ledger

`CostLedger` records Sol, Codex, DeepSeek, Claude, and future Fable usage under one session/candidate ledger.

Each event captures:

- session
- actor/model/stage
- candidate
- input tokens
- cached input tokens
- cache-write tokens
- output tokens
- observed USD when available

When observed USD is unavailable, estimation requires an explicit versioned `PriceCard`; model prices are not silently hard-coded into policy.

Key metrics to derive from the ledger:

- USD / merged PR
- USD / frozen candidate
- USD / reviewer stage
- USD / material defect found
- USD / useful code change
- Sol calls / PR
- Codex jobs and turns / PR
- reviewer calls / PR
- cost before first patch
- cost lost to blocked workers
- cost lost to repeated context reconstruction

## Context duplication analyzer

`context_duplication_ratio` uses deterministic SHA-256 chunk digests. It measures repeated bytes without an AI call and provides the baseline needed to shrink large Sol/Codex/reviewer contexts.

A high prompt-cache hit ratio is not treated as proof that a large prompt is economically efficient.

## `SOL_DECISION_PACKET_V1`

The packet intentionally contains only decision-relevant state:

- exact candidate identity
- risk tier/reasons
- workflow state and last event
- exact decision required
- active contract
- open semantic questions
- changed files and diff summary
- open/resolved/disputed findings
- QG and reviewer summaries
- relevant source slices
- remaining budget
- allowed transitions
- `production_authority=false`

It excludes unrelated repository history, roadmap, PRs, issues, and broad snapshots by default.

## `CODEX_TASK_CAPSULE_V1`

The controller prepares before Codex:

- exact source SHA
- exact historical/reference SHA when needed
- materialization state
- changed-file allowlist
- forbidden files
- bounded contract
- exact findings
- acceptance tests
- relevant source slices
- relevant tests
- historical delta

The worker should not spend model turns rediscovering PR numbers, SHAs, file locations, or historical materialization already known deterministically.

If evidence is missing, the model-facing protocol is structured:

`NEED_EVIDENCE(symbol/file/test)`

The controller may then obtain that evidence deterministically before one bounded continuation.

## `REVIEW_FREEZE_PACKAGE_V1`

Reviewer optimization is not hash-only. The package includes:

- BASE/HEAD/TREE/SYNTHETIC via candidate identity
- reviewer role and contract version
- changed-file manifest
- exact diff
- exact changed-file contents
- semantic dependency slices
- architecture invariants
- exact QG evidence
- adversarial evidence
- relevant prior finding closure evidence
- questions
- prohibited conclusions

Prior reviewer verdicts are not authority. Independent reviewers must reproduce findings independently.

## Shadow risk classifier

Risk tiers:

- T0 mechanical/documentation/generated metadata
- T1 local low-risk implementation
- T2 semantic contract
- T3 security/governance/authority/Risk/execution-sensitive
- T4 release/Production-sensitive

This first implementation is intentionally fail-closed upward on explicit markers and is `shadow_only=true`. It cannot suppress a reviewer.

## Shadow cost scheduler

Deterministic outputs:

- `CALL_MODEL`
- `NO_CALL_REQUIRED`
- `WAIT`
- `HUMAN_DECISION_REQUIRED`
- `BUDGET_STOP`

Before an eligible model call, the controller must be able to represent:

- semantic uncertainty
- model role
- candidate ID
- required evidence
- expected information gain
- estimated tokens
- estimated USD
- remaining budget
- invalidation rule

In shadow mode these decisions are measured only; they do not alter live dispatch.

## Preauthorized clean-pass plan

The deterministic controller may calculate a next stage only when all are true:

1. the completed stage is in the preauthorized plan;
2. the verdict is an explicit clean verdict;
3. exact candidate is unchanged;
4. evidence is complete;
5. no anomaly exists.

A material finding, blocked validation, evidence insufficiency, contradiction, candidate mutation, or anomaly returns control to semantic adjudication rather than auto-advancing.

This implementation does **not** yet wire clean-pass routing into live dispatch.

## Fable Full Red-Team architecture

Fable is a Supreme Red-Team Auditor, not a repository navigator.

Shadow audit modes:

- `NONE`: ordinary T0/T1 change
- `DELTA`: semantic T2 delta
- `CROSS_BOUNDARY`: T3, security/governance, or cross-boundary change
- `FULL_SYSTEM`: milestone, release/recertification, or T4

A Fable package is hash-bound and contains:

- system freeze across repositories
- exact primary candidate
- changed-since-last-audit manifest
- dependency graph
- authority graph
- trust boundaries
- data flows
- AI orchestration graph
- contracts/invariants/forbidden transitions
- QG evidence
- known attack surfaces
- source/symbol indexes
- cross-component interfaces
- prior immutable audit evidence references
- hard USD budget

The package instructs Fable not to trust prior conclusions and to attempt to falsify guarantees.

### Fable output contract

Findings are compact and reproducible:

- FINDING_ID
- SEVERITY
- AFFECTED_COMPONENT
- EXACT_FILE_OR_SYMBOL
- VIOLATED_INVARIANT
- REPRODUCIBLE_WITNESS
- ATTACK_OR_FAILURE_PATH
- EXPECTED
- ACTUAL
- SMALLEST_SAFE_FIX
- CONFIDENCE
- EVIDENCE_REFERENCES

Long narrative summaries are not required.

### Fable cost preflight

`preflight_fable_cost_shadow` estimates the audit before dispatch from explicit:

- stable tokens
- changed tokens
- cross-boundary tokens
- expected output tokens
- expected cache-hit ratio
- optional batch discount
- current explicit price card
- hard budget USD

No model call is needed to produce the estimate.

A future live Fable dispatcher must be fail-closed when the preflight is over budget or when the immutable package cannot be built.

### Finding compaction

Before Sol adjudication, findings can be deterministically grouped into:

- REPRODUCED
- DISPROVED
- DUPLICATE
- SEMANTIC_DISPUTE
- UNVERIFIED

This supports one high-value Sol adjudication rather than one Sol call per Fable finding.

## Zero-model-call shadow report

`scripts/build_economic_shadow_report.py` combines candidate identity, risk classification, cost ledger, context duplication, current full reviewer plan, Fable selection, and Fable cost preflight into one deterministic report.

It reads local JSON, performs no network access, and has no model-dispatch capability.

## Migration gates

### Phase A — deterministic/free

Implemented foundation:

- unified cost ledger
- candidate identity
- evidence ledger
- context duplication analyzer
- shadow risk classifier
- shadow cost scheduler primitives
- Fable cost preflight
- zero-model-call shadow report

Still required before live routing activation:

- historical Actions artifact normalization at scale
- cache-write/cache-hit attribution dashboard
- residual polling audit
- historical replay corpus

### Phase B — packet/context

Implemented contract foundations:

- Sol decision packet
- Codex task capsule
- review freeze package
- Fable audit package

Still required before live activation:

- deterministic source/symbol slicer
- stable cache-prefix integration
- historical replay proving no decision-quality regression

### Phase C — routing

State-machine primitives exist only in shadow mode. No live dispatcher behavior changes in this unit.

### Phase D — reviewer economy

Not activated. Reviewer-stage suppression is prohibited until enough historical/live evidence demonstrates unique-defect yield, overlap, false positives, severity, and post-adjudication survival by risk tier.

### Phase E — controlled paid validation

Not performed by this unit. A future controlled run requires explicit budget and preserved no-hidden-retry guarantees.

### Phase F — economic recertification

Routing reductions may be promoted only if quality, defect detection, fail-closed behavior, auditability, and independence remain at least baseline while measured cost is materially lower.

## Non-goals

This change does not:

- call or rearm Sol;
- call Codex;
- dispatch DeepSeek;
- dispatch Claude;
- dispatch Fable;
- change qore-core;
- lower tests or review quality;
- authorize Production or real capital;
- change existing autonomy spend caps;
- silently replace any independent reviewer.
