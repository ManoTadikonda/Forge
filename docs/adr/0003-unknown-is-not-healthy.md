# ADR 0003: Unknown health never promotes a release

- Status: Accepted
- Date: 2026-10-21

## Context

A canary controller asks: "is the candidate healthy enough to receive more
traffic?" There are three possible answers, not two — healthy, unhealthy, and
*not enough information to say*.

The third is easy to get wrong. The natural implementation checks
`error_rate > threshold` and promotes otherwise. That silently treats these as
successes:

- a candidate receiving no traffic at all (0 errors out of 0 requests is a 0%
  error rate, arithmetically)
- a candidate whose metrics endpoint is broken
- a candidate that has served three requests
- telemetry that stopped arriving ten minutes ago

Each of those is a plausible symptom of a *badly* broken release. A release that
crashes on startup produces no metrics — and a threshold check reads that as
perfect health and promotes it to 100%.

## Decision

Health analysis returns one of three verdicts, and only `HEALTHY` promotes.

```
HEALTHY    positive evidence: enough fresh samples, all within thresholds
UNHEALTHY  measured breach, or a failing liveness/readiness probe
UNKNOWN    insufficient, stale, or absent evidence
```

`UNKNOWN` is produced when: there are no samples; all samples are older than the
staleness window; or fewer than `min_samples` requests were observed.

At a canary step, `UNKNOWN` holds — and if the observation window expires while
still `UNKNOWN`, the rollout **aborts**. Silence cannot promote, and it also
cannot stall a rollout indefinitely.

Two supporting rules:

- **A failing probe bypasses the sample minimum.** A service reporting "I am not
  ready" is a direct statement about itself; it needs no statistical support.
- **Probe volume scales with canary weight.** At 10%, only a tenth of requests
  reach the candidate. A fixed probe count leaves it below `min_samples`, so every
  verdict is `UNKNOWN` and every rollout aborts for the wrong reason. This project
  shipped that bug and fixed it: probe volume is now `min_samples / weight ×
  safety factor`.

## Consequences

Positive:

- A release that fails to start cannot be promoted by its own silence.
- Broken telemetry fails safe rather than fails open.
- The distinction appears in the UI: `UNKNOWN` renders as a warning, never as
  success, and the dashboard shows a null success rate rather than 0% when nothing
  has run.
- The reason is always specific: "7 requests observed, 20 required" rather than
  "health check failed".

Negative, and accepted:

- **A low-traffic service may never produce a verdict**, and its rollouts will
  abort. That is the correct outcome — Forge genuinely cannot tell whether such a
  release is safe — but it means `min_samples` must be tuned per service, and a
  truly idle service needs synthetic traffic.
- **Aborting on `UNKNOWN` can roll back a release that was fine**, if telemetry
  broke rather than the service. Rolling back a working release costs a
  deployment; promoting a broken one costs an outage.
- **Three states are more code** than a boolean, in the analyser, the controller,
  the schema and the UI.

## Alternatives rejected

- **Treat unknown as healthy.** Fails open on exactly the worst cases.
- **Treat unknown as unhealthy immediately.** Too aggressive: a brief scrape gap
  at the start of a window would abort a healthy rollout. Hold-then-abort gives
  the candidate the full window to produce evidence.
- **Block forever on unknown.** Turns a missing metric into a permanently stuck
  deployment holding a lock, which is an outage of a different shape.
