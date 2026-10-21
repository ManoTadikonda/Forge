# Forge — Implementation Plan & Status

Tracked checklist. Updated as work lands. `[x]` means implemented **and** executed.

## Environment baseline (verified 2026-10-21 on this host)

| Capability | Status | Evidence |
| --- | --- | --- |
| Python 3.12.15 | available | `python3.12 --version` |
| PostgreSQL 16.2 | **real**, embedded via `pgserver` | `scripts/probe_pg.py` printed server version |
| Redis 7.2.5 | **real**, built from source | `scripts/build_redis.sh`, `redis-cli ping` -> PONG |
| Node 24.12 / npm 11.6 | available | `npm view react version` -> 19.3.1 |
| PyPI / npm network | available | package installs succeed |
| Docker / BuildKit | **absent** | no `docker`/`podman`/`colima`/`nerdctl`, no package manager |
| Terraform / AWS | **absent** | no `terraform` binary, no credentials |

Consequence: container-based build & deploy are implemented as adapters behind ports but
**cannot be executed here**. The process-based adapters are real and are what the demo runs.
See `docs/adr/0001-environment-constrained-adapters.md`.

## Checklist

### Foundation
- [ ] Monorepo layout + per-package installable projects
- [ ] `forge_core`: config, structured logging, redaction, errors, ids
- [ ] State machines (pipeline, deployment, job) with central transition tables
- [ ] Ports: builder, registry, deployment adapter, key-value store, clock
- [ ] SSRF-resistant git URL validation
- [ ] Pipeline manifest schema

### Data layer
- [ ] SQLAlchemy 2 async models (users, roles, repos, envs, services, pipelines,
      stages, jobs, leases, artifacts, deployments, deployment events, health samples,
      incidents, audit events, webhook deliveries, log chunks)
- [ ] Alembic migration from empty database
- [ ] Durable queue: `SELECT ... FOR UPDATE SKIP LOCKED`, leases, heartbeats, backoff

### Execution
- [ ] Worker runtime with lease renewal + crash recovery
- [ ] Local content-addressed registry (digest-pinned artifacts)
- [ ] Local process builder (rlimits, scrubbed env, timeouts) + BuildKit adapter (unverified)
- [ ] SBOM generation
- [ ] Pipeline handler: clone -> validate -> test -> build -> publish

### Delivery
- [ ] Weighted routing proxy with admin API and per-request accounting
- [ ] Local process deployment adapter + Docker adapter (unverified)
- [ ] Deployment controller: canary steps, observation windows, promotion
- [ ] Health analyzer: error rate, p50/p95/p99, min-sample gating, stale telemetry
- [ ] Automated rollback with post-rollback HTTP verification

### API & web
- [ ] FastAPI `/api/v1` with auth, RBAC, rate limiting, security headers, OpenAPI
- [ ] Signed GitHub webhook ingestion + dedup
- [ ] React + TS strict + Vite + Tailwind + TanStack Query dashboard (11 pages)

### Verification
- [ ] Unit tests
- [ ] Integration tests against real PostgreSQL + Redis
- [ ] Reliability tests (crash, duplicate delivery, concurrency, restart)
- [ ] Healthy deployment demo
- [ ] Faulty release -> real rollback demo
- [ ] CI workflows
- [ ] Documentation set
