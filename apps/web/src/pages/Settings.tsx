import { useSettings } from '@/hooks/queries'
import { useAuth } from '@/lib/auth'
import {
  ErrorState, Field, Loading, PageHeader, Tone,
} from '@/components/primitives'

function Row({
  label, value, tone, hint,
}: {
  label: string
  value: string
  tone?: 'ok' | 'warn' | 'danger' | 'neutral'
  hint?: string
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b
                    border-surface-border px-4 py-3 last:border-0">
      <div>
        <div className="text-sm text-ink">{label}</div>
        {hint && <div className="mt-0.5 text-2xs text-ink-faint">{hint}</div>}
      </div>
      <div className="shrink-0">
        {tone ? <Tone tone={tone}>{value}</Tone> : (
          <span className="mono text-ink-muted">{value}</span>
        )}
      </div>
    </div>
  )
}

export function SettingsPage() {
  const { user } = useAuth()
  const { data, isLoading, error, refetch } = useSettings()

  if (isLoading && !data) return <Loading />
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return null

  return (
    <div>
      <PageHeader
        title="Settings"
        description="Operational configuration. Secret values are never returned by the API —
                     only whether each one is configured."
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card" aria-labelledby="platform-heading">
          <div className="border-b border-surface-border px-4 py-3">
            <h2 id="platform-heading" className="text-sm font-medium text-ink">
              Platform
            </h2>
          </div>
          <Row label="Environment" value={data.environment} />
          <Row label="Version" value={data.version} />
          <Row
            label="Builder"
            value={data.builder}
            hint={
              data.builder === 'process'
                ? 'Workspace isolation with resource limits — not a kernel sandbox'
                : 'Container builds via BuildKit'
            }
          />
          <Row label="Registry" value={data.registry} />
          <Row
            label="Deployment adapter"
            value={data.deploy_adapter}
            hint={
              data.deploy_adapter === 'process'
                ? 'Releases run as supervised local processes'
                : 'Releases run as containers'
            }
          />
          <Row label="Router admin" value={data.router_admin_url} />
        </section>

        <section className="card" aria-labelledby="rollout-heading">
          <div className="border-b border-surface-border px-4 py-3">
            <h2 id="rollout-heading" className="text-sm font-medium text-ink">
              Rollout policy
            </h2>
          </div>
          <Row label="Canary steps" value={data.canary_steps.join('% → ') + '%'} />
          <Row
            label="Observation window"
            value={`${data.canary_step_seconds}s per step`}
          />
          <Row
            label="Minimum samples"
            value={String(data.health_min_samples)}
            hint="Below this, health is UNKNOWN and the rollout never promotes"
          />
          <Row
            label="Max error rate"
            value={`${(data.health_max_error_rate * 100).toFixed(1)}%`}
          />
          <Row
            label="Max p95 latency"
            value={`${data.health_max_p95_latency_ms.toFixed(0)}ms`}
          />
        </section>

        <section className="card" aria-labelledby="security-heading">
          <div className="border-b border-surface-border px-4 py-3">
            <h2 id="security-heading" className="text-sm font-medium text-ink">
              Security
            </h2>
          </div>
          <Row
            label="Webhook signing secret"
            value={data.webhook_secret_configured ? 'Configured' : 'Not configured'}
            tone={data.webhook_secret_configured ? 'ok' : 'warn'}
            hint={
              data.webhook_secret_configured
                ? 'Deliveries must carry a valid HMAC signature'
                : 'Webhook ingestion is disabled until a secret is set'
            }
          />
          <Row
            label="Rate limiting"
            value={
              data.rate_limit_enabled
                ? `Enabled (${data.rate_limiter_backend})`
                : 'Disabled'
            }
            tone={data.rate_limit_enabled ? 'ok' : 'warn'}
            hint={
              data.rate_limiter_backend === 'in-memory'
                ? 'Per-process counters — limits are not shared across API replicas'
                : 'Shared counters across replicas'
            }
          />
          <Row
            label="Redis"
            value={data.redis_configured ? 'Configured' : 'Not configured'}
            tone={data.redis_configured ? 'ok' : 'neutral'}
          />
          <Row
            label="Unsandboxed builds"
            value={data.unsandboxed_builds_allowed ? 'Allowed' : 'Blocked'}
            tone={data.unsandboxed_builds_allowed ? 'warn' : 'ok'}
            hint={
              data.unsandboxed_builds_allowed
                ? 'Repository code runs without kernel-level isolation. Acceptable for ' +
                  'local development only.'
                : 'The process builder is disabled; a container builder is required'
            }
          />
        </section>

        <section className="card card-pad" aria-labelledby="account-heading">
          <h2 id="account-heading" className="label mb-3">
            Your account
          </h2>
          <dl className="space-y-3">
            <Field label="Email">{user?.email ?? '—'}</Field>
            <Field label="Role">{user?.role ?? '—'}</Field>
            <Field label="Permissions">
              <ul className="mt-1 flex flex-wrap gap-1">
                {(user?.permissions ?? []).map((permission) => (
                  <li key={permission}>
                    <code className="mono rounded bg-surface px-1.5 py-0.5
                                     text-ink-muted">
                      {permission}
                    </code>
                  </li>
                ))}
              </ul>
            </Field>
          </dl>
          <p className="mt-3 text-2xs text-ink-faint">
            Permissions are enforced on the server for every request. Hiding a control
            in this interface is a convenience, not a security boundary.
          </p>
        </section>
      </div>
    </div>
  )
}
