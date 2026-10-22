# Forge — implementation status

Everything marked `[x]` was implemented **and executed**. Items that were written
but could not be run on this host are listed separately, with the reason.

## Environment baseline (verified on the development host)

| Capability | Status | Evidence |
| --- | --- | --- |
| Python 3.12.15 | available | `python3.12 --version` |
| PostgreSQL 16.2 | **real**, embedded via `pgserver` | server version queried; all integration tests run against it |
| Redis 7.2.5 | **real**, built from source | `scripts/build_redis.sh`; `redis-cli ping` → PONG |
| Node 24.12 / npm 11.6 | available | dashboard builds and tests |
| PyPI / npm | reachable | dependency installs succeed |
| Docker / BuildKit | **absent** | no `docker`/`podman`/`colima`/`nerdctl`, no package manager |
| Terraform / AWS | **absent** | no binary, no account, no credentials |

## Delivered and verified

### Foundation
- [x] Monorepo with five installable packages and a working dev install path
- [x] `forge_core`: settings with production safety validation, structured JSON
      logging with correlation context, secret redaction, domain errors
- [x] Central state machines for pipeline, deployment and job, with illegal
      transitions rejected
- [x] Ports: builder, registry, deployment adapter, key-value store, clock
- [x] SSRF-resistant repository URL validation
- [x] Pipeline manifest schema with an executable allowlist and no shell

### Data layer
- [x] 18 SQLAlchemy 2 async tables
- [x] Alembic migrations applying from an empty database, reversible
- [x] Durable queue: `FOR UPDATE SKIP LOCKED`, leases, heartbeats, fence tokens,
      exponential backoff with full jitter, dead-letter

### Execution
- [x] Worker runtime with lease renewal, crash recovery sweeper, graceful shutdown
- [x] Content-addressed local registry; artifacts immutable by construction
- [x] Process builder with scrubbed environment, POSIX rlimits, process-group kill
      and bounded output — disabled unless explicitly enabled
- [x] Reproducible artifacts: identical source yields an identical digest
- [x] CycloneDX 1.5 SBOM generation
- [x] Pipeline: clone → validate → test → build → SBOM → scan → publish
- [x] Isolated git environment, so checkout never reads the host's git config

### Delivery
- [x] Weighted routing proxy with exact distribution and separate admin listener
- [x] Process deployment adapter running real releases on real ports
- [x] Deployment controller: canary steps, per-window counter baselining,
      promotion, cancellation
- [x] Health analyser: error rate, p50/p95/p99, min-sample gating, staleness,
      probe failures, regression against stable
- [x] Weight-scaled probe traffic so low canary weights still produce a verdict
- [x] Automated rollback with post-rollback HTTP verification
- [x] PostgreSQL deployment locks with fencing

### API and web
- [x] 39 routes under `/api/v1` with OpenAPI, consistent error envelopes
- [x] Argon2id credentials, JWT sessions with server-side revocation, lockout
- [x] RBAC enforced server-side on every route
- [x] CSRF for cookie-authenticated writes, security headers, explicit CORS
- [x] Rate limiting with Redis, degrading to per-process and reporting which
- [x] Signed GitHub webhook ingestion with delivery deduplication
- [x] Operator CLI: seed, version bump, user creation, runtime reset
- [x] React 19 + TS strict dashboard, 13 routes, state-driven polling

### Verification
- [x] 225 unit tests
- [x] 80 integration tests against real PostgreSQL
- [x] 13 reliability tests (crash, fencing, duplicates, concurrency, restart)
- [x] 9 sample-service tests, 17 frontend unit tests
- [x] 22 Playwright tests against a live stack
- [x] `ruff`, `ruff format` and strict `mypy` clean across 76 source files
- [x] Healthy release demonstration
- [x] Faulty release → measured breach → verified rollback demonstration
- [x] Healthy canary promoting through 10 → 25 → 50 → 100%
- [x] Documentation set and five ADRs

## Written but not executed here

Each carries a status banner in its own file. None is claimed to work.

| Item | Why it could not run |
| --- | --- |
| `BuildKitBuilder` | No container runtime |
| `DockerDeploymentAdapter` | No container runtime |
| `EcrRegistry` | No AWS account or credentials |
| `infra/compose/` | No container runtime |
| `infra/terraform/` | No Terraform binary, no AWS account |
| `.github/workflows/` | Valid YAML; never executed on GitHub |

## Known limitations

Recorded in `docs/security.md` and the README, not buried here: the process
builder is not a kernel sandbox; SSRF protection is userspace and cannot fully
close the TOCTOU window; artifacts are unsigned; dependency scanning is optional
and reported as `SKIPPED` when absent; private repositories are unsupported;
there is no multi-tenancy.
