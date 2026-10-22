# The demonstration

```bash
make up && make seed && make demo
```

Roughly a minute. No container runtime, no cloud account, no network access.

## What it proves

1. A pipeline builds real source: clone, manifest validation, lint and tests
   executed in a sandboxed workspace, a reproducible artifact, an SBOM, publication.
2. The artifact deploys and actually serves traffic.
3. A second release that misbehaves is **detected from measurements** and rolled
   back, with recovery **verified by live HTTP** before the rollback is recorded.

## Setup

```bash
export FORGE_BOOTSTRAP_ADMIN_EMAIL=admin@forge.local
export FORGE_BOOTSTRAP_ADMIN_PASSWORD='choose-something-at-least-12-chars'

make up      # PostgreSQL, Redis, router, API, worker
make seed    # build and register the bundled demo repository
```

`make seed` creates a git repository from `apps/sample-service` under
`.forge/demo/demo-src` and registers it with a `file://` URL. **The HTTP API never
accepts a local URL** — only this operator command can create one. That is what
lets the demo run offline while the SSRF guard stays strict for user input.

## Run it

```bash
make demo              # both scenarios
make demo-healthy      # scenario 1 only
make demo-rollback     # scenario 2 only
```

The demo pins its own starting version, so it is reproducible from any prior
state — including a workspace left at some other version by an earlier run.

## Scenario 1 — a healthy release

```
▶ Building v1.0.0
    ✓ CLONE     Checked out e4fc459c7efc on main.
    ✓ VALIDATE  Manifest valid: service sample-service, 2 check(s).
    ✓ TEST      2 check(s) passed.
    ✓ BUILD     Built sha256:f0085442ade3… (30720 bytes).
    ✓ SBOM      CycloneDX 1.5 SBOM with 3 component(s).
    - SCAN      No dependency scanner is installed (looked for trivy, grype, pip-audit).
    ✓ PUBLISH   Published forge.local/sample-service@sha256:f0085442ade3…

▶ Deploying v1.0.0
    candidate_started     Candidate 1.0.0 is running at http://127.0.0.1:50461.
    candidate_verified    Candidate passed its pre-traffic health and readiness checks.
    initial_release       No previous release exists, so traffic moves directly to it.
    promoted              Initial release 1.0.0 is live.

▶ Verifying with real traffic through the router
  responses by version: {'1.0.0': 40}
  ✓ all 40 requests served by v1.0.0
```

Worth noticing:

- **`TEST` really ran.** `ruff` and 9 `pytest` tests executed inside the build
  sandbox. The output is in the dashboard under Build output.
- **`SCAN` is `SKIPPED`, not passed.** No scanner is installed, and Forge says so
  with the reason rather than quietly showing green.
- **The digest is content-addressed.** Identical source always produces
  `sha256:f0085442ade3…`; the archive uses normalised metadata so timestamps and
  the building user do not change it.
- **The first release skips the canary**, because there is no previous version to
  shift traffic away from. A canary against nothing would be theatre.

## Scenario 2 — a faulty release

```
▶ Committing v1.1.0 to the demo repository and building it
  ✓ artifact sha256:21741bb5dbba…
  version 1.1.0 — builds and tests pass; the fault is injected at deploy time

▶ Deploying v1.1.0 with a 50% injected error rate
    candidate_started     Candidate 1.1.0 is running at http://127.0.0.1:51377.
    candidate_verified    Candidate passed its pre-traffic health and readiness checks.
    canary_step           Shifted 10% of traffic to the candidate.
    health_evaluated      UNHEALTHY: error rate 45.45% exceeds ceiling 5.00%.
    rollback_started      Rolling back: Health thresholds breached…
    traffic_restored      All traffic routed back to the previous release 1.0.0.
    candidate_stopped     Candidate release was stopped.
    rollback_verification traffic served by 1.0.0 = True, stable health probe = True.
    rollback_finished     Traffic restored to the previous healthy release and verified.

▶ Outcome
  ✓ state: ROLLED_BACK
  ✓ rollback verified by post-rollback HTTP check: True
  reason: Health thresholds breached: error rate 45.45% exceeds ceiling 5.00%.

▶ Verifying traffic was actually restored
  router weights: {'sample-service-1.0.0-…': 100, 'sample-service-1.1.0-…': 0}
  responses by version: {'1.0.0': 40}
  ✓ all 40 requests served by the restored v1.0.0
  ✓ no requests reached the faulty candidate

▶ Incident record
  HIGH — Automated rollback completed
  verdict=UNHEALTHY requests=33 errors=15 error_rate=0.46875
  breaches=['error_rate']
```

The sequence that matters:

1. **The candidate is healthy before traffic.** It passes its readiness and health
   checks — the fault only appears under load, which is the realistic case.
2. **10% of traffic reaches it.** Really: the weighted proxy routes requests, and
   the candidate's own counters agree with the router's.
3. **Measurement, not assertion.** 33 requests, 15 errors, 45.45% against a 5%
   ceiling. The number comes from scraping the running service.
4. **Abort is immediate.** A breach does not wait out the observation window.
5. **Traffic is restored, then the candidate is stopped** — in that order, so no
   request is dropped into a dying process.
6. **Recovery is verified.** Forge issues fresh HTTP requests and checks which
   version answers, then probes the restored release. Only then is `ROLLED_BACK`
   written. Had either check failed, the state would be `ROLLBACK_FAILED`.
7. **An incident records the evidence**, not just the outcome.

### The fault is explicit, not random

`SAMPLE_ERROR_RATE=0.5` is passed to the candidate at deploy time via
`release_env`. The sample service uses a per-process counter, not a random number
generator, so a 50% error rate fails exactly every second request. A reproducible
demo needs a reproducible fault — and the faulty behaviour is configuration, never
something hidden in the source.

## See it in the dashboard

```bash
make web      # http://127.0.0.1:5173
```

- **Overview** — real success rate, recent runs, the open incident
- **Pipelines → a run** — stage timeline, build output, artifact digest, SBOM count
- **Deployments → the rolled-back one** — the rollback banner with its reason and
  verification, rollout progression aborted at 10%, candidate health `UNHEALTHY`
  with its breach badge against stable `HEALTHY`, error-rate and latency charts,
  and the full event timeline
- **Security & SBOM** — provenance, the incident, the honest `SKIPPED` scan
- **Audit log** — every sign-in and deployment, including failures

## Variations

```bash
# A healthy second release promotes through every canary step
curl -sX POST http://127.0.0.1:8000/api/v1/deployments \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"artifact_digest":"sha256:...","environment":"development",
       "service_name":"sample-service","canary_steps":[10,25,50,100],"step_seconds":3}'
```

Observed: `10% HEALTHY → 25% HEALTHY → 50% HEALTHY → 100% HEALTHY → promoted`.

Other fault modes the sample service supports:

| Variable | Effect | Expected verdict |
| --- | --- | --- |
| `SAMPLE_ERROR_RATE=0.5` | One request in two returns 500 | `UNHEALTHY` — `error_rate` |
| `SAMPLE_LATENCY_MS=2000` | Every request takes 2s | `UNHEALTHY` — `latency_p95` |
| `SAMPLE_UNHEALTHY=true` | `/health` and `/ready` fail | `UNHEALTHY` — `probe_failed`, aborts before traffic |
| `SAMPLE_STARTUP_DELAY_SECONDS=120` | Never becomes ready | Aborted at the readiness gate |

## Verifying it yourself

The claims are checkable without trusting the summary:

```bash
# Who is actually serving?
curl -sD- -o /dev/null http://127.0.0.1:8098/sample-service/development/api/work \
  | grep -i x-forge

# The router's own view of weights and counters
curl -s http://127.0.0.1:8099/routes | python3 -m json.tool

# Each release's own request counters, independent of the router
curl -s http://127.0.0.1:<release-port>/api/stats | python3 -m json.tool

# The durable record, independent of both
curl -s http://127.0.0.1:8000/api/v1/deployments \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Three independent sources for the traffic split — client headers, router counters,
backend counters — and a fourth for the decision, in PostgreSQL.

## Durability

```bash
make down && make up
```

Pipelines, deployments, events, health samples, incidents and audit records are
all still there. The router comes up empty by design: it holds live routing state
in memory and the controller reasserts it, so authority flows from the database
outward.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Could not sign in` | Bootstrap variables not set | Export both, then `make restart` |
| `The demo repository is not registered` | Seed not run | `make seed` |
| `Builder unavailable: … does not provide kernel-level isolation` | Safety default | `make up` sets `FORGE_ALLOW_UNSANDBOXED_BUILDS=true`; exporting it yourself overrides |
| `Executable 'pytest' was not found on the build PATH` | Toolchain not visible to builds | Set `FORGE_BUILD_TOOLCHAIN_PATH` to the directory providing it |
| `Router admin API … is unreachable` | Router not running | `make status`, then `make restart` |
| Rollback says `Insufficient traffic to judge` | `min_samples` too high for the step | Lower `FORGE_HEALTH_MIN_SAMPLES` or raise the first canary weight |
| `A deployment is already in progress` | A previous rollout is stuck | `make status`; cancel it via the API, or `make reset-runtime` |
| Port already in use | Something else holds 8000/8098/8099/6399 | Override `FORGE_API_PORT`, `FORGE_ROUTER_PORT`, `FORGE_REDIS_PORT` |

Logs are in `.forge/logs/`. For a completely clean slate:

```bash
make clean    # deletes all local data
make up && make seed && make demo
```
