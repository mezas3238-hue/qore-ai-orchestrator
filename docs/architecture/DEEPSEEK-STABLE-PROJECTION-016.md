# DeepSeek Stable Projection 016

## Purpose

Autonomous V2 run `33327713413` stopped fail-closed because the live DeepSeek meter selected `compact-budgeted` / v20 by default while the sole stable governance manifest still declared `QORE-DEEPSEEK-V2.1.1-STABLE`. There was no governed promotion evidence making v20 a stable successor.

DeepSeek PR #25 resolved that contradiction by restoring V2.1.1 as the ordinary default, retaining v20 only as an explicit alternate, and porting the exact-QG authenticated compact transport into the stable path.

This orchestrator unit makes that resolution independently reconstructible by Sol.

## Contract

The reviewer control-plane collector now:

- requires exactly one live `QORE-DEEPSEEK-*-STABLE.json` manifest;
- requires that manifest to be `QORE-DEEPSEEK-V2.1.1-STABLE`, `status=stable`, model `deepseek-v4-pro`;
- requires the meter default and ordinary route to match the stable manifest;
- verifies every engine/workflow/meter/QG/alternate Git blob declared by the manifest against the exact DeepSeek `main` SHA;
- verifies the stable entrypoint carries the exact-QG helper and the V2.1 reasoning invariants;
- verifies the live review workflow still binds package, BASE, HEAD, synthetic parents/tree and pre-publication freeze;
- records compact-budgeted v20 and benchmark candidate routes as non-default, non-promoted alternates;
- fails closed on multiple STABLE manifests, blob drift, model drift, routing drift or QG-contract drift.

Sol receives the full bounded governance projection. Codex receives only the compact operational subset needed to avoid duplicate work and understand reviewer identity; manifest file inventories and historical prose remain architect-only.

## Authority boundary

This unit changes no `qore-core` code, grants no Production or real-capital authority, does not weaken QORE quality gates, and does not treat workflow success as semantic reviewer PASS.
