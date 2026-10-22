# API

Versioned under `/api/v1`. Interactive documentation is generated from the code:

- Swagger UI — http://127.0.0.1:8000/docs
- ReDoc — http://127.0.0.1:8000/redoc
- OpenAPI schema — http://127.0.0.1:8000/openapi.json

## Conventions

**Authentication.** A bearer token or a session cookie. The cookie is set by
`/auth/login` and is `HttpOnly`, so browser clients never handle the token
directly. Cookie-authenticated writes additionally require the CSRF token in an
`X-CSRF-Token` header.

**Errors.** One envelope, everywhere:

```json
{
  "error": {
    "code": "unsafe_url",
    "message": "Repository URL targets a disallowed network.",
    "details": { "host": "169.254.169.254" }
  },
  "request_id": "0f6f9f1e-7a4e-4c2e-9f7a-2f7b8a1d33ce"
}
```

`code` is stable and safe to branch on. `request_id` appears on every response as
`X-Request-ID` and is the handle for finding the full server-side detail — an
internal error returns only this, never a stack trace.

| Status | `code` | Meaning |
| --- | --- | --- |
| 401 | `authentication_required` | Missing, invalid, expired or revoked session |
| 403 | `forbidden` | Authenticated, but the role lacks the permission |
| 404 | `not_found` | No such resource |
| 409 | `conflict` | Already exists, or an operation is already in progress |
| 409 | `illegal_transition` | The requested state change is not legal |
| 422 | `validation_error` | Request body or parameters are invalid |
| 422 | `unsafe_url` | Repository URL failed egress validation |
| 429 | `rate_limited` | Too many requests |

**Pagination.** List endpoints take `limit` (1–200, default 25) and `offset`, and
return `{ items, total, limit, offset }`.

**Idempotency.** Pipeline and deployment creation accept `idempotency_key`.
Repeating a request with the same key returns the original resource instead of
starting a second build or rollout.

---

## Authentication

```bash
# Sign in
curl -sX POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@forge.local","password":"your-password"}'
```

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_at": "2026-10-22T01:30:00Z",
  "csrf_token": "l6sww8ZP4NmH..."
}
```

```bash
TOKEN=$(curl -sX POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@forge.local","password":"your-password"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -s http://127.0.0.1:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
```

| Method | Path | Permission | Notes |
| --- | --- | --- | --- |
| POST | `/auth/login` | — | Rate-limited; locks out after repeated failures |
| POST | `/auth/logout` | authenticated | Revokes the session server-side, immediately |
| GET | `/auth/me` | authenticated | Returns the user and their resolved permissions |

---

## Repositories

```bash
curl -sX POST http://127.0.0.1:8000/api/v1/repositories \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"https://github.com/psf/requests","default_branch":"main"}'
```

Public HTTPS only. These are all rejected with `422 unsafe_url`:

```bash
http://github.com/a/b                      # not HTTPS
file:///etc/passwd                         # not a remote scheme
https://user:pass@github.com/a/b           # embedded credentials
https://127.0.0.1/x                        # loopback
https://10.0.0.1/x                         # private range
https://169.254.169.254/latest/meta-data/  # cloud metadata
https://[::ffff:127.0.0.1]/x               # IPv4-mapped loopback
```

| Method | Path | Permission |
| --- | --- | --- |
| POST | `/repositories` | `repo:write` |
| GET | `/repositories` | `repo:read` |
| GET | `/repositories/{id}` | `repo:read` |
| DELETE | `/repositories/{id}` | `repo:delete` |

Deletion is a soft delete: pipeline history, artifacts and audit records reference
the repository and must remain readable.

---

## Pipelines

```bash
curl -sX POST http://127.0.0.1:8000/api/v1/pipelines \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"repository_id":"<uuid>","branch":"main","idempotency_key":"build-42"}'
```

| Method | Path | Permission | Notes |
| --- | --- | --- | --- |
| POST | `/pipelines` | `pipeline:trigger` | 409 if one is already running for the repository |
| GET | `/pipelines` | `pipeline:read` | Filter by `repository_id`, `state`, `branch` |
| GET | `/pipelines/{id}` | `pipeline:read` | Includes stages and artifacts |
| GET | `/pipelines/{id}/logs` | `pipeline:read` | Paginated, redacted |
| POST | `/pipelines/{id}/cancel` | `pipeline:cancel` | Idempotent on a finished run |

A run detail response carries the full stage timeline:

```json
{
  "state": "SUCCEEDED",
  "commit_sha": "e4fc459c7efc...",
  "duration_ms": 776,
  "stages": [
    { "name": "CLONE",    "status": "SUCCEEDED", "duration_ms": 39 },
    { "name": "TEST",     "status": "SUCCEEDED", "message": "2 check(s) passed." },
    { "name": "SCAN",     "status": "SKIPPED",
      "skip_reason": "No dependency scanner is installed (looked for trivy, grype, pip-audit)." },
    { "name": "PUBLISH",  "status": "SUCCEEDED" }
  ],
  "artifacts": [
    { "digest": "sha256:f0085442ade3...", "version": "1.0.0", "signed": false }
  ]
}
```

A skipped gate reports **why** it was skipped. It is never reported as passing.

---

## Deployments

```bash
curl -sX POST http://127.0.0.1:8000/api/v1/deployments \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
        "artifact_digest": "sha256:f0085442ade3...",
        "environment": "development",
        "service_name": "sample-service",
        "canary_steps": [10, 25, 50, 100],
        "step_seconds": 20
      }'
```

`release_env` passes environment variables to the candidate — this is how the
demo injects its fault:

```json
{ "release_env": { "SAMPLE_ERROR_RATE": "0.5" } }
```

| Method | Path | Permission | Notes |
| --- | --- | --- | --- |
| POST | `/deployments` | `deployment:create` | 409 if one is active for the service |
| GET | `/deployments` | `deployment:read` | Filter by `service_id`, `state` |
| GET | `/deployments/{id}` | `deployment:read` | Events, health samples, canary plan |
| GET | `/deployments/{id}/events` | `deployment:read` | `?after=N` for incremental polling |
| POST | `/deployments/{id}/cancel` | `deployment:cancel` | Idempotent |
| POST | `/deployments/{id}/rollback` | `deployment:rollback` | Creates a *new* deployment of the previous artifact |

Manual rollback is expressed as a new deployment rather than a mutation, so it
goes through the same verified path as any other release and the history records
both events.

A rolled-back deployment:

```json
{
  "state": "ROLLED_BACK",
  "rollback_verified": true,
  "rollback_reason": "Health thresholds breached: error rate 45.45% exceeds ceiling 5.00%.",
  "current_weight": 10,
  "health_summary": {
    "candidate": { "verdict": "UNHEALTHY", "requests": 33, "errors": 15,
                   "error_rate": 0.454545, "breaches": ["error_rate"] },
    "stable":    { "verdict": "HEALTHY",   "requests": 274, "errors": 0 }
  }
}
```

`rollback_verified` is the field that matters: `true` means Forge issued live HTTP
requests after restoring traffic and confirmed the previous release answered them.

---

## Platform, artifacts and governance

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| GET | `/overview` | `pipeline:read` | Dashboard aggregates |
| GET | `/environments` | `deployment:read` | Configured environments |
| GET | `/services` | `deployment:read` | Services, filterable by environment |
| GET | `/services/{id}/health` | `deployment:read` | Live routing weights read from the router |
| GET | `/incidents` | `deployment:read` | Rollback and failure records |
| GET | `/artifacts` | `pipeline:read` | Artifacts with provenance |
| GET | `/artifacts/{id}` | `pipeline:read` | One artifact |
| GET | `/artifacts/{id}/sbom` | `pipeline:read` | CycloneDX 1.5 download |
| GET | `/audit` | `audit:read` | Audit events, filterable |
| GET | `/admin/settings` | `settings:read` | Configuration, **no secret values** |
| GET | `/admin/users` | `user:manage` | Users |
| POST | `/admin/users` | `user:manage` | Create a user |
| POST | `/admin/users/{id}/deactivate` | `user:manage` | 409 on self |

`/services/{id}/health` reads weights from the router itself rather than from
Forge's own records. Reporting intent as if it were reality would hide a drift
bug; if the router is unreachable the response says so.

---

## Webhooks

```bash
BODY='{"ref":"refs/heads/main","repository":{"clone_url":"https://github.com/you/app.git"}}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')"

curl -sX POST http://127.0.0.1:8000/api/v1/webhooks/github \
  -H "X-GitHub-Event: push" \
  -H "X-GitHub-Delivery: $(uuidgen)" \
  -H "X-Hub-Signature-256: $SIG" \
  -H 'Content-Type: application/json' \
  -d "$BODY"
```

- The signature is verified over the **raw bytes**, before any parsing.
- An unset `FORGE_GITHUB_WEBHOOK_SECRET` **disables** the endpoint. Unsigned
  deliveries are never accepted.
- `X-GitHub-Delivery` is recorded with a unique constraint, so a retry is
  acknowledged (`{"status":"duplicate"}`) but never rebuilds.
- Only pushes to the default branch and PR open/synchronize/reopen trigger builds;
  anything else is acknowledged and ignored with a reason.

---

## Operational endpoints

| Path | Auth | Purpose |
| --- | --- | --- |
| `/health` | none | Liveness. Cheap; does not touch the database |
| `/ready` | none | Readiness. Queries the database; 503 when degraded |
| `/metrics` | none | Prometheus exposition |

`/health` and `/ready` are deliberately different. A load balancer that cannot
distinguish them will drain a healthy node during a transient database blip.

## Rate limits

| Scope | Default | Setting |
| --- | --- | --- |
| Login | 10/min per IP | `FORGE_RATE_LIMIT_LOGIN_PER_MINUTE` |
| Writes | 60/min per IP | `FORGE_RATE_LIMIT_WRITE_PER_MINUTE` |
| Webhooks | 120/min per IP | `FORGE_RATE_LIMIT_WEBHOOK_PER_MINUTE` |

Backed by Redis when configured, otherwise per-process counters — weaker across
replicas, and `/ready` reports which backend is active.
