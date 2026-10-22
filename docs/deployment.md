# Deployment

Three paths, with very different levels of verification. The status line on each
is the most important part of this document.

| Path | Status |
| --- | --- |
| Local processes | **Verified.** This is how the demo runs. |
| Containers (Compose) | **Never executed.** No container runtime on the development host. |
| AWS (Terraform) | **Never applied.** `terraform validate` in CI only. |

---

## Local (verified)

```bash
python3.12 -m venv .venv
make setup

export FORGE_BOOTSTRAP_ADMIN_EMAIL=admin@forge.local
export FORGE_BOOTSTRAP_ADMIN_PASSWORD='at-least-twelve-characters'

make up && make seed && make demo
```

No container runtime, no cloud account, no network access required. PostgreSQL 16
runs embedded; Redis 7 is built from source once. Both persist under `.forge/`.

| Component | Where |
| --- | --- |
| PostgreSQL | `.forge/pgdata` (unix socket) |
| Redis | `.tooling/redis`, data in `.forge/redis` |
| Artifacts | `.forge/artifacts` (content-addressed) |
| Workspaces | `.forge/workspaces` (deleted after each build) |
| Logs | `.forge/logs` |
| Secret key | `.forge/secret_key`, generated once, mode 600, gitignored |

`make down` stops services and keeps data. `make clean --yes` deletes everything.

---

## Containers (unverified)

```bash
cp deploy/.env.example deploy/.env
# set POSTGRES_PASSWORD and FORGE_SECRET_KEY
docker compose -f infra/compose/docker-compose.yml --env-file deploy/.env up -d
```

**This has never been run.** The files are written against documented image
behaviour and each carries a status banner. Expect to debug it.

What the stack is designed to do:

| Service | Notes |
| --- | --- |
| `postgres` | Not published to the host; only Forge services reach it |
| `redis` | Not published |
| `buildkit` | Isolated build daemon. The worker talks to it over its own address and **never** receives the host container socket |
| `migrate` | Runs once; `api` and `worker` wait for it to complete successfully |
| `api` | Read-only root filesystem, dropped capabilities, `no-new-privileges` |
| `worker` | Mounts the container socket — see the warning below |
| `router` | **Only the public port is published.** The admin API, which mutates traffic weights, stays internal |
| `web` | nginx serving static assets and proxying `/api` so the session cookie is first-party |

> **The socket mount on `worker` is the most dangerous line in that file.**
> Anything that can talk to a container runtime socket can start a privileged
> container mounting the host filesystem — that is root. It is there because the
> Docker deployment adapter needs it to start releases. Set
> `FORGE_DEPLOY_ADAPTER=process` if you do not want it. The **build** worker never
> gets it: builds go through BuildKit over its own endpoint.

### Before trusting it

1. `docker compose config` to validate.
2. Build each image individually and check the non-root user took effect.
3. Confirm `migrate` completes before `api` starts.
4. Confirm the router admin port is **not** reachable from outside.
5. Run `scripts/demo.py` against it.

---

## AWS (never applied)

Full detail in [`infra/terraform/README.md`](../infra/terraform/README.md).

```
Internet ──▶ ALB (public subnets, HTTPS)
                │
          ECS Fargate (private subnets)
          ├── api ×N, worker ×N
                │
                ├──▶ RDS PostgreSQL (private, encrypted, Multi-AZ in production)
                ├──▶ ElastiCache Redis (private, TLS)
                └──▶ ECR (immutable tags, scan on push)
```

```bash
cd infra/terraform
terraform init \
  -backend-config="bucket=your-tfstate-bucket" \
  -backend-config="key=forge/dev.tfstate" \
  -backend-config="region=eu-west-1" \
  -backend-config="encrypt=true" \
  -backend-config="dynamodb_table=your-lock-table"

cp environments/dev.tfvars.example environments/dev.tfvars
terraform plan -var-file=environments/dev.tfvars    # read it carefully
```

Deliberate choices:

- **Images must be pinned by digest.** A variable validation rejects a mutable tag
  outright — an irreproducible deployment is the opposite of what Forge is for.
- **Secrets come from Secrets Manager**, injected as ECS `secrets`, so values
  never appear in a task definition, an image or `docker inspect`.
- **Only the load balancer is public.** Nothing running application code or
  storing data has an inbound route.
- **`/ready` is the health check**, not `/health`, so a task that cannot reach the
  database is drained rather than left serving errors.
- **Deployment circuit breaker with rollback.** This is ECS protecting the control
  plane; it is separate from Forge's own canary rollbacks, which protect the
  applications Forge deploys.

### After a first apply

1. Set the webhook secret — it starts empty, so webhook ingestion is disabled
   rather than accepting unsigned deliveries.
2. Run migrations as a one-off ECS task with the same image and secrets.
3. Create the first administrator with `forge_api.cli create-user`. There is no
   bootstrap administrator outside development; `Settings` refuses it.

### Known gaps

No WAF. Broad task egress (`0.0.0.0/0`) — Forge's SSRF guard is userspace, and a
network egress policy is the proper defence. No automated secret rotation. No DNS
or certificate automation. The router has no ECS service definition: traffic
splitting on AWS would need weighted ALB target groups or the router as its own
service.

---

## Configuration

Every setting is documented in [`.env.example`](../.env.example). The ones that
matter most:

| Variable | Required | Notes |
| --- | --- | --- |
| `FORGE_SECRET_KEY` | outside development | ≥32 chars. No default. Startup fails without it |
| `FORGE_DATABASE_URL` | outside development | Startup fails without it |
| `FORGE_GITHUB_WEBHOOK_SECRET` | for webhooks | Unset **disables** the endpoint |
| `FORGE_ALLOW_UNSANDBOXED_BUILDS` | for the process builder | Defaults to `false`; weaker isolation is opt-in |
| `FORGE_CORS_ALLOW_ORIGINS` | for browser clients | Wildcard refused outside development |
| `FORGE_BOOTSTRAP_ADMIN_*` | development only | Refused outside development |

`Settings` validates at startup and refuses to run with an unsafe production
configuration — a missing secret key, a wildcard CORS origin, or bootstrap
credentials outside development. Failing at startup beats failing in production.

## Production readiness

Honestly: **Forge is not production-ready for arbitrary multi-tenant workloads,**
and nothing here should be read as claiming otherwise.

What it does demonstrate, verified by execution: durable orchestration with
at-least-once delivery and idempotent handlers, real progressive delivery with
measured health gating, verified automated rollback, and state that survives
restarts.

What would be needed before trusting it with real services:

1. Container-based build isolation, actually exercised (the port exists; it has
   never been run).
2. Network-level egress policy for builds.
3. Multi-tenancy — today any authorised user sees everything.
4. Artifact signing and verification.
5. Secret rotation.
6. Load testing and capacity planning; none has been done.
7. The AWS path actually applied and operated.
