# Development

## Setup

```bash
python3.12 -m venv .venv
make setup        # Python deps, frontend deps, verifies every workspace import
```

Then, in each shell you work in:

```bash
source scripts/env.sh
```

That exports `PYTHONPATH` for the workspace packages and points
`FORGE_PG_DATA_DIR` at the local database. Every `make` target sources it itself,
so you only need it for ad-hoc commands.

### Why PYTHONPATH instead of `pip install -e`

Each package is a real distribution with its own `pyproject.toml`, so CI and
container images use a normal `pip install .`. Local development uses
`PYTHONPATH` because of a concrete problem: setuptools' editable install writes a
path-configuration file named `__editable__.<dist>.pth`, and the interpreter this
project was developed on **does not process `.pth` files at all** — neither
import-hook lines nor plain path lines. The editable install was silently inert:
no error, no import.

`PYTHONPATH` is explicit, portable, inherited by subprocesses, and cannot fail
silently. `conftest.py` additionally inserts the package roots so `pytest` works
with no environment setup at all.

## Running the stack

```bash
make up        # PostgreSQL, Redis, router, API, worker
make status
make logs
make down      # stop, keep data
make clean     # stop and DELETE all data (requires --yes)
```

`make up` is idempotent and never discards data. `make clean` is the only
destructive command, and it says so.

Services and ports:

| Service | Port | Notes |
| --- | --- | --- |
| API | 8000 | `/docs`, `/health`, `/ready`, `/metrics` |
| Router (public) | 8098 | Proxies to releases |
| Router (admin) | 8099 | Mutates weights — separate port on purpose |
| Redis | 6399 | Non-default, to avoid colliding with a system Redis |
| PostgreSQL | unix socket | Embedded, under `.forge/pgdata` |
| Dashboard | 5173 | `make web` |

## Local datastores

No container runtime is required.

**PostgreSQL 16** runs as an embedded server via `pgserver`, with its data in
`.forge/pgdata`. It is a real PostgreSQL — `SELECT ... FOR UPDATE SKIP LOCKED`,
partial indexes, JSONB and CHECK constraints all behave exactly as in production,
which is why the integration tests are meaningful.

**Redis 7** is built from source on first use by `scripts/build_redis.sh` (a few
minutes, once) into `.tooling/redis`.

To use external servers instead, set `FORGE_DATABASE_URL` and `FORGE_REDIS_URL`.

## Migrations

```bash
make migrate                          # upgrade to head
make migration m="add widget table"   # autogenerate
make migrate-down                     # roll back one
```

Autogenerate is a starting point, not an answer. Always read the generated file:
Alembic does not detect renames (it emits drop + add, which loses data), server
defaults, or data migrations.

CI verifies three things: that migrations apply to an empty database, that
`downgrade base` then `upgrade head` works, and that autogenerate produces **no**
changes — a non-empty diff means the models and migrations have drifted.

## Tests

```bash
make test               # everything Python
make test-unit          # no database needed
make test-integration   # real PostgreSQL
make test-reliability   # crash, duplicates, concurrency, restart
make test-web           # frontend units
make e2e                # Playwright, needs a running stack
```

| Suite | Count | What it covers |
| --- | --- | --- |
| `tests/unit` | 225 | State machines, health analysis, canary decisions, backoff, redaction, SSRF validation, manifest allowlisting, passwords, tokens, balancer |
| `tests/integration` | 80 | Queue concurrency, locking, API auth and RBAC, webhooks — against real PostgreSQL |
| `tests/reliability` | 13 | Worker death, fencing, duplicate delivery, racing deployments, restart durability |
| `apps/sample-service/tests` | 9 | The sample service, including its deterministic fault modes |
| `apps/web/src/**/*.test.ts` | 17 | Formatting and state-mapping logic |
| `apps/web/e2e` | 22 | Browser tests against a live stack |

Integration tests deliberately use a real database. `SKIP LOCKED`, row locking and
transactional visibility *are* the behaviour under test; a fake would test
nothing.

**The test database is separate from the development one.** `conftest.py` forces
`FORGE_PG_DATA_DIR` to `.forge/pgdata-test` and refuses to run if the target
resolves to the development directory — the integration fixtures `TRUNCATE` every
table, and this project learned that lesson the direct way.

## Code conventions

**Python.** Ruff (lint + format, 100 columns) and strict mypy, both clean across
the tree. Enabled rule families include `S` (security), `B` (bugbear), `ASYNC`
(blocking calls in async contexts) and `SIM`.

Specific habits worth matching:

- No blocking I/O in `async def`. Wrap it in `asyncio.to_thread` — `ASYNC240`
  catches `pathlib` calls, and it is usually right.
- Never hold a transaction across a build, a sleep or a network call.
- Mutating API routes call `await commit(session)` before returning. Dependency
  teardown is a safety net, not the contract — see `forge_api/deps.py` for why.
- A `noqa` carries a reason. If the reason is weak, fix the code instead; several
  suppressions in this project were replaced by better code (the queue's dynamic
  SQL became a bound parameter, `git` became an absolute path).

**TypeScript.** Strict mode with `noUncheckedIndexedAccess`, ESLint with
type-aware rules, zero warnings allowed. API response types are hand-maintained in
`src/lib/types.ts`: the surface is small, and an explicit type is where a
client/server drift shows up as a compile error.

## Debugging

**Logs** are JSON with correlation ids bound to the context:

```bash
tail -f .forge/logs/worker.log | python3 -m json.tool
grep '"deployment_id": "<uuid>"' .forge/logs/*.log
```

For readable local output: `export FORGE_LOG_FORMAT=console`.

**Inspect the database:**

```bash
source scripts/env.sh
.venv/bin/python - <<'PY'
import asyncio
from sqlalchemy import text
from forge_core.db.session import init_engine, embedded_database_url, session_scope
async def main():
    init_engine(embedded_database_url())
    async with session_scope() as s:
        for row in await s.execute(text(
            "SELECT state, count(*) FROM deployments GROUP BY state")):
            print(row)
asyncio.run(main())
PY
```

**Inspect the router**, which is the ground truth for traffic:

```bash
curl -s http://127.0.0.1:8099/routes | python3 -m json.tool
curl -sD- -o /dev/null http://127.0.0.1:8098/sample-service/development/api/work \
  | grep -i x-forge
```

**Reset without losing users or repositories:**

```bash
make reset-runtime
```

## Common tasks

Add an API endpoint:

1. Response schema in `apps/api/forge_api/schemas.py` — never return an ORM object.
2. Route in the appropriate `routers/` module, depending on
   `require(Permission.X)`.
3. `await commit(session)` if it mutates.
4. A test in `tests/integration/test_api.py`, including the denial case.

Add a deployment adapter:

1. Implement `DeploymentAdapter` from `forge_core/ports.py`.
2. `available()` must never raise; it returns `(usable, reason)` and the reason is
   shown to operators.
3. Register it in `forge_worker/runtime.py:build_adapter`.
4. No orchestration code should need to change. If it does, the port is wrong.
