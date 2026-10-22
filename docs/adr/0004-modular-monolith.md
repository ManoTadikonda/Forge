# ADR 0004: A modular monolith plus workers, not microservices

- Status: Accepted
- Date: 2026-10-21

## Context

Forge has recognisable subsystems: repository management, pipeline execution,
artifact storage, deployment orchestration, health analysis, audit. A platform
project is under some pressure to split these into services — it looks like
serious architecture, and it demonstrates familiarity with distributed systems.

It would also be the wrong decision here, and the reasoning is worth recording
because "we split it because that is what platforms look like" is how a lot of
unnecessary complexity enters a system.

## Decision

Four processes, each with an independent reason to exist:

| Process | Why separate |
| --- | --- |
| API | Must serve requests with predictable latency; cannot be blocked by a multi-minute build |
| Worker | Runs long, failure-prone work; scales and restarts independently, and its crashes must not take the API down |
| Router | Sits in the data path of deployed applications; its lifecycle belongs to them, not to the control plane |
| Dashboard | Static assets, no server runtime to operate |

Everything else is a module inside those processes. There is no artifact service,
no pipeline service, no health service.

The test applied: **would this boundary change how the system fails or scales?**
If splitting a module only adds a network hop, it is not a boundary — it is a
latency and failure budget spent on nothing.

Internal boundaries are kept honest with *ports* instead (`ArtifactBuilder`,
`ArtifactRegistry`, `DeploymentAdapter`). These give substitutability — the entire
point of ADR 0001 — without distributed-systems overhead. The proof the boundary
is real is that swapping the process deployment adapter for the Docker one changes
no orchestration code.

## Consequences

Positive:

- The transactional guarantees in ADR 0002 are *available*. A pipeline row and its
  job commit together because they are in the same database and the same
  transaction. Split them across services and that becomes a saga.
- A deployment either happened or did not. No partial cross-service state to
  reconcile.
- Debugging follows a stack trace rather than a trace id across five services.
- Local development is four processes, which is why the demo needs no container
  runtime.
- Refactoring across module boundaries is a compiler-checked rename, not a
  coordinated multi-repository release.

Negative, and accepted:

- **Coarse scaling.** The API scales as a unit. If repository listing became hot
  it could not be scaled separately from webhook ingestion. At Forge's scale this
  is irrelevant; at a different scale it would not be.
- **A shared failure domain within each process.** A memory leak in one module
  affects every module in that process.
- **One deployable means one release cadence** for everything in it.
- **Module boundaries are enforced by discipline**, not by the network. They can
  erode. The API importing from the worker was exactly that erosion, caught during
  development and fixed by moving the shared helpers into `forge_core`.

## Alternatives rejected

- **Service per subsystem.** Would turn every multi-step operation into a
  distributed transaction, for a system with no independent scaling need and one
  team.
- **Single process for everything, including workers.** Simpler still, but a build
  would block the API's event loop, and a worker crash would take the control
  plane with it. The API/worker split is the one that pays for itself.
- **Serverless functions per step.** Attractive for burst workloads, but the
  deployment controller is a long-lived stateful orchestration holding a lock
  across many steps — the shape functions are worst at.
