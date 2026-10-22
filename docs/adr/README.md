# Architectural decision records

Each record states a decision, the reasoning behind it, and what it costs. The
"Consequences" sections are the useful part: a decision without an articulated
downside usually means the alternatives were not seriously considered.

| ADR | Decision |
| --- | --- |
| [0001](0001-environment-constrained-adapters.md) | Ports with multiple adapters, so orchestration is real where containers are not available |
| [0002](0002-postgres-backed-queue.md) | The durable job queue lives in PostgreSQL, not a message broker |
| [0003](0003-unknown-is-not-healthy.md) | Unknown health never promotes a release |
| [0004](0004-modular-monolith.md) | A modular monolith plus workers, not microservices |
| [0005](0005-verified-rollback.md) | A rollback is not complete until recovery is verified by live traffic |
