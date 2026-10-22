# ADR 0005: A rollback is not complete until recovery is verified

- Status: Accepted
- Date: 2026-10-21

## Context

The headline capability of this project is automated rollback. That makes it the
feature most worth being rigorous about, because it is also the easiest to fake:

```python
if unhealthy:
    deployment.state = "ROLLED_BACK"   # looks identical in the dashboard
```

A status field proves nothing. Worse, a *partially successful* rollback is more
dangerous than no rollback at all, because the dashboard says the incident is
over while traffic may still be reaching the broken release.

Real ways a rollback can fail after the decision is made:

- the routing change is issued but the router rejects or drops it
- weights are restored, but the previous release has also died
- the candidate is stopped before traffic moves, dropping in-flight requests
- there is no previous release to restore

## Decision

Rollback is a sequence of infrastructure actions, each verified, with a terminal
state that reflects what was actually confirmed:

1. **Persist the evidence first.** The measurements that justified the rollback
   are written before acting on them.
2. Transition to `ROLLING_BACK`, recording the reason.
3. Shift 100% of traffic to the previous release.
4. **Then** stop the candidate — in that order, so no request is routed into a
   dying process.
5. **Verify by issuing live HTTP requests** through the router and checking which
   version answers.
6. Probe the restored release's own health endpoint.
7. Write `ROLLED_BACK` **only if both checks pass**; otherwise `ROLLBACK_FAILED`.
8. Raise an incident carrying the evidence either way.

`ROLLBACK_FAILED` is a first-class terminal state, not an error log. It is styled
distinctly in the dashboard and has its own runbook, because it is the state that
needs a human.

The state machine enforces this: `ROLLING_BACK` can only reach `ROLLED_BACK` or
`ROLLBACK_FAILED`. There is deliberately no edge to `SUCCEEDED`.

## Consequences

Positive:

- `rollback_verified: true` means something checkable: live requests were served
  by the restored version.
- The failure case is visible rather than silently mislabelled as success.
- "No previous release exists" is reported honestly instead of being dressed up as
  a successful rollback.
- The evidence survives. Storing it only after the abort decision meant it was
  missing from precisely the deployments that rolled back — a bug this project had
  and fixed.

Negative, and accepted:

- **Rollback takes longer.** Verification adds a round of real traffic. During an
  incident that is seconds of additional exposure — worth it, because the
  alternative is not knowing whether the incident is over.
- **Verification can itself fail spuriously.** A network blip during the check
  produces `ROLLBACK_FAILED` for a rollback that actually worked. A false alarm
  that summons a human is preferable to a false all-clear.
- **More states to handle** in the controller, the schema, the API and the UI.
- **The controller must stay alive through verification.** If it dies mid-rollback
  the deployment is left in `ROLLING_BACK`; the runbook covers reconciling that
  against the router, which is the ground truth for traffic.

## Alternatives rejected

- **Set the status and move on.** Fast, and proves nothing.
- **Verify asynchronously afterwards.** Leaves a window where the deployment reads
  `ROLLED_BACK` while nothing has been confirmed — the exact ambiguity this
  decision exists to remove.
- **Trust the router's acknowledgement.** Better than nothing, but it confirms the
  weight change was accepted, not that the restored release is serving. Those come
  apart precisely when the previous release has also failed.
