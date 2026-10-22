# ADR 0001: Adapter boundaries for build, registry, and deployment

- Status: Accepted
- Date: 2026-10-21
- Revised: 2026-10-21 — Redis and PostgreSQL moved from fallback to verified

## Context

Forge must build artifacts from untrusted repository source and deploy them with real
traffic routing, health analysis, and rollback.

The reference design calls for BuildKit-backed container builds and container-based
deployment. The development host used to build and verify this project has:

- No container runtime. `docker`, `podman`, `colima`, `nerdctl` and Docker Desktop are all
  absent, and no package manager (Homebrew/MacPorts) is installed to obtain one.
- No system PostgreSQL or Redis, and no Terraform binary or AWS credentials.

Two of those gaps turned out to be closable, and were closed rather than worked around:

- **PostgreSQL 16.2** runs as an embedded server via `pgserver`. It is a genuine
  PostgreSQL, so `SELECT ... FOR UPDATE SKIP LOCKED`, partial indexes, JSONB and CHECK
  constraints behave exactly as in production — which is what makes the integration
  tests worth running.
- **Redis 7.2.5** builds from source (`scripts/build_redis.sh`); a working C compiler is
  present. An earlier revision of this ADR assumed compilation was unavailable and
  planned to rely on the in-memory fallback. That assumption was wrong, and testing it
  was cheaper than designing around it.

The container runtime gap is real and not closable here.

These are properties of the host, not of the design. The question is how to keep the
delivery semantics real and verifiable while remaining honest about what was executed.

## Decision

Define three ports and supply more than one adapter for each:

| Port | Adapter | Status on this host |
| --- | --- | --- |
| `ArtifactBuilder` | `LocalProcessBuilder` | Verified by execution |
| | `BuildKitBuilder` | Implemented, **not executed** (no daemon) |
| `ArtifactRegistry` | `LocalContentAddressedRegistry` | Verified by execution |
| | `EcrRegistry` | Implemented, **not executed** (no AWS account) |
| `DeploymentAdapter` | `LocalProcessAdapter` | Verified by execution |
| | `DockerAdapter` | Implemented, **not executed** (no daemon) |
| `KeyValueStore` | `RedisRateLimiter` | Verified by execution (Redis 7.2.5 built from source) |
| | `InMemoryRateLimiter` | Verified by execution (single-process fallback) |

The controller, state machines, health analyzer, and rollback logic sit entirely above
these ports and are adapter-agnostic. Swapping `LocalProcessAdapter` for `DockerAdapter`
changes no orchestration code.

## What "real" means for the local adapter

The local path is not a simulation. Concretely:

- Artifacts are content-addressed. The builder produces a reproducible tar stream and
  records its `sha256:...` digest. Deployments reference that digest, never a mutable tag.
- Releases run as real OS processes bound to real, dynamically allocated TCP ports.
- Traffic is routed by a real weighted reverse proxy. Routing weights are enforced per
  request, and the resulting distribution is measured from the proxy's own counters and
  from the sample service's request counters — two independent sources.
- Health checks are real HTTP requests against the running release.
- Rollback terminates the candidate's processes and reweights the proxy to the previous
  healthy release, then re-verifies by issuing live HTTP traffic.

A rollback is only recorded as `ROLLED_BACK` after post-rollback HTTP verification
succeeds against the restored release.

## Consequences

Positive:

- The orchestration logic — the genuinely interesting part — is fully exercised and tested.
- The demo is reproducible on any machine with Python, with no Docker requirement.
- The container path is a drop-in adapter rather than a rewrite.

Negative, and explicitly accepted:

- **Build isolation is weaker than containers.** `LocalProcessBuilder` isolates by
  workspace directory, a scrubbed environment, a process group, POSIX resource limits
  (`RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_NPROC`, `RLIMIT_FSIZE`), and a wall-clock timeout.
  It does **not** provide a kernel-level sandbox. It does not contain a determined
  attacker who controls repository contents. This is documented in `docs/security.md`
  and the builder refuses to run unless `FORGE_ALLOW_UNSANDBOXED_BUILDS=true` is set
  explicitly, so the weaker mode can never be entered by accident.
- `BuildKitBuilder`, `DockerAdapter`, `EcrRegistry`, the Compose stack and the Terraform
  stack are unverified. Each carries a status banner in its own file, and the README
  lists them. They must not be described as working.
- The fallback adapters are not dead weight kept for appearances: the process adapters
  are what the demonstration actually runs, and the container adapters exist to show the
  port is genuine. Swapping `LocalProcessAdapter` for `DockerAdapter` changes no
  orchestration code — that is the test of whether the boundary is real.

## Alternatives rejected

- *Mock the container calls and report success.* Rejected: fabricates evidence.
- *Ship only the Docker adapter and leave the demo unrunnable.* Rejected: the central
  claim of the project — automated health-driven rollback — would be unverifiable.
- *Use Redis as coordination source of truth.* Rejected independently of the host: durable
  state belongs in PostgreSQL. Redis is a cache/rate-limit/ephemeral-lock accelerator only.
