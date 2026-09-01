# Harness candidate materialization

This repository contains a deterministic, no-model materialization lane for Integration-Authority-accepted Harness candidate artifacts.

The lane is fail-closed: a request binds the exact target repository, open draft pull request, starting HEAD and tree, patch digest, expected resulting Git blob identities, and a non-main target branch. It validates those bindings before applying a patch and revalidates the live branch immediately before publication.

Materialization is a mechanical transfer step only. It does not establish semantic certification, merge readiness, operational readiness, Production authority, or real-capital authority. Any materialized Core candidate must receive a fresh Core quality gate and a fresh semantic review chain bound to its new immutable freeze.
