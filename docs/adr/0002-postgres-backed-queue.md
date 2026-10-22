# ADR 0002: The durable job queue lives in PostgreSQL

- Status: Accepted
- Date: 2026-10-21

## Context

Forge needs durable background work: pipelines that run for minutes and
deployments that mutate live infrastructure. The obvious options were a dedicated
broker (Redis, RabbitMQ, SQS) or the database already in use.

The decisive property is not throughput. Forge's job rate is low — a handful per
minute at most. It is **what happens when a job is lost.**

A lost pipeline job wastes a build. A lost *deployment* job means Forge has
started a candidate release, shifted traffic to it, and then forgotten it exists.
Nothing will advance the canary, nothing will evaluate health, and nothing will
roll back. Traffic keeps flowing to a release no one is watching.

## Decision

The queue is a table in the same PostgreSQL database as the state it mutates.
Jobs are claimed with `SELECT ... FOR UPDATE SKIP LOCKED`.

The property this buys is **transactional enqueue**: the row that justifies a job
and the job itself are committed in one transaction.

```python
async with session.begin():
    session.add(deployment)
    await queue.enqueue(session, kind=RUN_DEPLOYMENT, dedupe_key=f"deployment:{id}")
```

Either both exist or neither does. With an external broker there is always a
window where one exists without the other, in both directions:

- commit the row, then fail to publish → a deployment nothing will ever run
- publish, then fail to commit → a worker claims a job whose row does not exist

The usual fixes are a transactional outbox or two-phase commit. An outbox is a
table in the database polled by a relay — at which point you have built this
design plus an extra hop.

Redis is still used, for rate-limit counters and caching: data whose loss costs
throughput, not correctness.

## Consequences

Positive:

- A job cannot exist without its row, or vice versa. Verified: a caller that rolls
  back after enqueuing leaves no orphan.
- One datastore to operate, back up and restore. A backup is internally consistent
  by construction.
- Queue state is queryable with SQL — "which jobs are stuck and why" is a `SELECT`,
  not a broker-specific CLI.
- Dedup, leases, fencing and backoff are all columns, visible and testable.

Negative, and accepted:

- **Polling, not push.** Workers poll at `worker_poll_interval_seconds`, so there
  is up to that much latency before a job starts. At Forge's scale this is
  invisible; at thousands of jobs per second it would not be. `LISTEN/NOTIFY`
  could remove it without changing the model.
- **Queue load sits on the primary.** A high-volume queue would contend with
  application queries. The partial index on runnable jobs keeps the claim cheap,
  but this does not scale indefinitely.
- **No native fan-out or routing.** There is one queue and a `kind` filter. Topic
  routing or consumer groups would have to be built.

## Alternatives rejected

- **Redis as the queue.** Fast and simple, but durability is weaker and, more
  importantly, it cannot participate in the PostgreSQL transaction. The failure
  mode is the one this ADR exists to avoid.
- **SQS or similar.** Durable and operationally easy, but still a separate system
  with the same atomicity gap, plus a cloud dependency the local demo cannot have.
- **Transactional outbox with a relay.** Correct, and the right answer at a scale
  where the broker earns its place. Here it adds a moving part to reach the same
  guarantee this design has directly.
