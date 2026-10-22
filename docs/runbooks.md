# Runbooks

Each runbook assumes `source scripts/env.sh` has been run.

## Orientation

```bash
make status                                   # what is running
curl -s localhost:8000/health   | python3 -m json.tool
curl -s localhost:8000/ready    | python3 -m json.tool   # 503 = degraded
curl -s localhost:8099/health   | python3 -m json.tool   # router
tail -n 100 .forge/logs/*.log
```

`/health` says the process is alive. `/ready` says it can serve — it queries the
database. If `/ready` is failing but `/health` is not, the problem is downstream,
not the API.

---

## A worker is not picking up jobs

**Symptoms.** Pipelines stay `QUEUED`; deployments stay `PENDING`.

1. **Is it running?**
   ```bash
   make status
   tail -50 .forge/logs/worker.log
   ```
   If stopped: `make restart`. Work is durable — nothing is lost.

2. **Is there anything to claim?**
   ```bash
   curl -s localhost:8000/metrics | grep forge_queue_depth
   ```
   All zero means the API never enqueued; look at the API log for the create call.

3. **Are jobs stuck `RUNNING` with dead workers?** The sweeper reclaims expired
   leases every 10 seconds. If nothing is recovering, the sweeper itself is wedged
   — check the worker log for `sweeper_failed` and restart.

4. **Are they in `DEAD_LETTER`?**
   ```sql
   SELECT id, kind, attempts, last_error FROM jobs
   WHERE state = 'DEAD_LETTER' ORDER BY updated_at DESC LIMIT 10;
   ```
   `last_error` is redacted but specific. Dead-lettered jobs are not retried
   automatically; that is deliberate — a poison job should not loop forever.

5. **Is a deployment lock blocking?**
   ```sql
   SELECT resource, owner, token, expires_at FROM deployment_locks;
   ```
   An expired lock is reclaimable automatically. A held one with a live owner means
   a rollout is genuinely in progress.

---

## A pipeline failed

1. Read the stage that failed — the dashboard shows the timeline, or:
   ```bash
   curl -s localhost:8000/api/v1/pipelines/<id> -H "Authorization: Bearer $TOKEN" \
     | python3 -m json.tool
   ```

| Stage | Common cause | Fix |
| --- | --- | --- |
| `CLONE` | Bad URL, branch missing, host unreachable | Check the branch exists; SSRF rejections are deliberate |
| `VALIDATE` | `forge.toml` missing or invalid | The message names the exact schema error |
| `TEST` | The repository's own tests failed, or exit 127 | 127 means the tool is not on the build PATH — set `FORGE_BUILD_TOOLCHAIN_PATH` |
| `BUILD` | Builder unavailable | Process builder needs `FORGE_ALLOW_UNSANDBOXED_BUILDS=true` |
| `PUBLISH` | Artifact root not writable | Check permissions on `.forge/artifacts` |

2. Logs are redacted but complete:
   ```bash
   curl -s localhost:8000/api/v1/pipelines/<id>/logs -H "Authorization: Bearer $TOKEN"
   ```

3. A timed-out build reports it explicitly. Raise `FORGE_BUILD_TIMEOUT_SECONDS` or
   the per-step `timeout_seconds` in the manifest.

**Builds are not retried automatically** (`max_attempts=1`). A failed build is a
result, not a transient fault. Fix the cause and trigger again.

---

## A deployment is stuck

1. **Which state?**
   ```bash
   curl -s localhost:8000/api/v1/deployments/<id> -H "Authorization: Bearer $TOKEN" \
     | python3 -m json.tool
   ```

| State | Meaning | Action |
| --- | --- | --- |
| `PENDING` | Not claimed | See the worker runbook |
| `PREPARING` | Resolving the artifact | Check it exists in the registry |
| `DEPLOYING` | Starting the candidate | Check the release log under `.forge/releases/<id>/` |
| `VERIFYING` | Waiting for readiness | The candidate is not becoming ready; 60s timeout then abort |
| `CANARY` | Observing | Normal. Each step takes `step_seconds` |
| `PROMOTING` | Final weight shift | Should be seconds |
| `ROLLING_BACK` | Restoring traffic | Should be seconds |

2. **Stuck in `CANARY` with `UNKNOWN` health** usually means the candidate is not
   receiving enough traffic to judge. Check `min_samples` against the step weight
   — probe volume scales with weight, but a very high `min_samples` with a 1% step
   can still starve it.

3. **Cancel it.** Cancellation is cooperative and idempotent:
   ```bash
   curl -sX POST localhost:8000/api/v1/deployments/<id>/cancel \
     -H "Authorization: Bearer $TOKEN"
   ```

4. **If the controller died mid-rollout**, the deployment stays in its last
   persisted state and the lock expires. Verify what is actually serving before
   doing anything:
   ```bash
   curl -s localhost:8099/routes | python3 -m json.tool
   ```
   The router is the ground truth for traffic. Forge's records are the ground truth
   for intent. If they disagree, trust the router and reconcile.

---

## A rollback failed (`ROLLBACK_FAILED`)

**This state means Forge could not confirm recovery. It needs a human.** It is
deliberately distinct from `ROLLED_BACK` and styled differently in the dashboard.

1. **Find out what is actually serving:**
   ```bash
   curl -s localhost:8099/routes | python3 -m json.tool
   for i in $(seq 1 10); do
     curl -sD- -o /dev/null localhost:8098/<service>/<env>/api/work | grep -i x-forge-version
   done
   ```

2. **Read the incident**, which carries the evidence and the verification result:
   ```bash
   curl -s localhost:8000/api/v1/incidents -H "Authorization: Bearer $TOKEN" \
     | python3 -m json.tool
   ```

3. **Common causes:**

| Cause | Check | Fix |
| --- | --- | --- |
| The previous release is also unhealthy | Probe it directly | Deploy a known-good artifact |
| The router lost its table (restart) | `GET /routes` returns nothing | Re-register by deploying a known-good artifact |
| No previous release existed | `previous_version` is empty | Nothing to restore; deploy a working version |
| Both releases are stopped | Check processes | Deploy a known-good artifact |

4. **Restore service manually** if needed, by deploying the last known-good digest:
   ```bash
   curl -sX POST localhost:8000/api/v1/deployments \
     -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"artifact_digest":"sha256:<known-good>","environment":"development",
          "service_name":"<service>","canary_steps":[100],"step_seconds":5}'
   ```
   A single 100% step is appropriate here: the target is already known good, and a
   slow canary prolongs the incident.

---

## The router is unreachable

Deployments fail at `set_traffic_weights`, and the Environments page says weights
cannot be confirmed.

```bash
make status
tail -50 .forge/logs/router.log
curl -s localhost:8099/health
```

Restart with `make restart`. **The router comes up with an empty table by design**
— it holds live routing state in memory, and the controller reasserts it. After a
router restart mid-rollout, nothing is being routed until a deployment runs. Deploy
the known-good digest to re-register the route.

---

## Database problems

**Connection failures.** `/ready` returns 503 while `/health` still returns 200,
so a load balancer drains the node rather than sending it traffic.

```bash
ls -la .forge/pgdata        # present?
tail -50 .forge/logs/api.log | grep -i database
```

**Migrations out of date:**
```bash
make migrate
```

**Recovery from backup** (local development has no automated backups; take one
before anything risky):
```bash
make down
cp -a .forge/pgdata .forge/pgdata.backup-$(date +%Y%m%d-%H%M%S)
```

**Last resort — destroy and rebuild.** This deletes everything:
```bash
make clean   # requires --yes
make up && make seed
```

---

## Investigating a stuck job

```sql
SELECT id, kind, state, attempts, max_attempts, worker_id,
       lease_expires_at, heartbeat_at, last_error
FROM jobs
WHERE state IN ('RUNNING', 'PENDING')
ORDER BY updated_at DESC
LIMIT 20;
```

| Pattern | Meaning |
| --- | --- |
| `RUNNING`, `lease_expires_at` in the past | Worker died; the sweeper will requeue within ~10s |
| `RUNNING`, heartbeat recent | Genuinely working |
| `PENDING`, `available_at` in the future | Backing off after a failure — expected |
| `attempts = max_attempts`, `FAILED` | Next sweep sends it to `DEAD_LETTER` |

To force a retry of a dead-lettered job, reset it deliberately — and understand
why it failed first:

```sql
UPDATE jobs SET state = 'PENDING', attempts = 0, available_at = now()
WHERE id = '<uuid>';
```

---

## Security incidents

**Suspected credential compromise:**

1. Rotate `FORGE_SECRET_KEY` and restart. This invalidates every session
   immediately.
2. Review the audit log, including failures:
   ```bash
   curl -s 'localhost:8000/api/v1/audit?outcome=FAILURE' \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
   ```
3. Deactivate affected accounts:
   ```bash
   curl -sX POST localhost:8000/api/v1/admin/users/<id>/deactivate \
     -H "Authorization: Bearer $TOKEN"
   ```

**A malicious repository was registered:**

1. Remove it (history is retained for investigation):
   ```bash
   curl -sX DELETE localhost:8000/api/v1/repositories/<id> \
     -H "Authorization: Bearer $TOKEN"
   ```
2. Review what its builds did. Build output is captured and redacted.
3. Remember the limitation: the process builder is **not** a kernel sandbox. If
   untrusted code ran under it, treat the host as potentially compromised. This is
   why containers are the production control.

**Webhook abuse.** Signature failures are logged with the delivery id. Rotate
`FORGE_GITHUB_WEBHOOK_SECRET` in both Forge and GitHub; unsigned deliveries are
already refused.

---

## Escalation checklist

Before concluding Forge is at fault, confirm in order:

1. `make status` — are all four services up?
2. `/ready` — is the database reachable?
3. `curl localhost:8099/routes` — what is actually being served?
4. `.forge/logs/*.log` — the correlation id ties a request across processes.
5. The deployment's event timeline — it records every decision and its reason.

Those five answer most questions, because every decision Forge makes is persisted
with the evidence behind it.
