# SOL Escalation Cost Fix 005

## Incident observed in architect cycle 33295683756

The optimized selector correctly chose `xhigh`, and the first GPT-5.6 Sol pass returned `NO_ACTION`, `next_actor=NONE`, no escalation request, and sufficient evidence to hold PR #466 frozen.

The deterministic escalation gate nevertheless retried at `max` because it scanned `risk_gates` for raw substrings and matched the word `production` inside the protective statement `Production and real-capital authority remain closed.`

That retry was unnecessary. It duplicated the bounded 33k-token input and produced the same architectural outcome.

## Correction

Escalation from a model decision is based on explicit structured signals first:

- an architect-requested higher tier;
- `HUMAN_DECISION_REQUIRED` -> `max` verification;
- `RECONSTRUCTION_REQUIRED` -> at least `xhigh`;
- active critical risk phrases rather than generic policy words.

Protective statements such as `Production remains closed`, `no Production authority`, `do not use real capital`, or generic references to credentials must not themselves trigger `max`.

The gate remains fail-closed for actual active critical conditions such as a credential/secret leak, Production activation request, real-capital exposure, split-brain, bypass-risk, security incident, or architectural/invariant contradiction.

## Non-regression

No QORE Core files, reviewer repos, provider credentials, review ordering, freeze/QG semantics, or Production authority are changed.
