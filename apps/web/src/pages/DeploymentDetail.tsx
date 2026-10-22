import { Link, useParams } from 'react-router-dom'
import {
  useCancelDeployment, useDeployment, useRollbackDeployment,
} from '@/hooks/queries'
import { useAuth } from '@/lib/auth'
import {
  Digest, EmptyState, ErrorState, Field, Loading, PageHeader, StateBadge, Tone,
} from '@/components/primitives'
import { HealthChart } from '@/components/HealthChart'
import { deploymentMeta, verdictTone } from '@/lib/state'
import {
  formatAbsolute, formatDuration, formatPercent, formatRelative, titleCase,
} from '@/lib/format'
import type { DeploymentDetail, DeploymentEvent, HealthAssessment } from '@/lib/types'

function CanaryProgress({ deployment }: { deployment: DeploymentDetail }) {
  const plan = deployment.canary_plan.length > 0 ? deployment.canary_plan : [100]
  const aborted = deployment.state.startsWith('ROLL')
  return (
    <div className="card card-pad">
      <div className="label mb-3">Rollout progression</div>
      <ol className="flex items-center gap-1.5">
        {plan.map((weight, index) => {
          const reached = index <= deployment.current_step_index
          const current = index === deployment.current_step_index
          const tone = aborted && current
            ? 'bg-danger'
            : reached
              ? 'bg-accent'
              : 'bg-surface-overlay'
          return (
            <li key={weight} className="flex-1">
              <div
                className={`h-1.5 rounded-full ${tone}`}
                role="img"
                aria-label={`${weight}% step ${
                  aborted && current ? 'aborted' : reached ? 'reached' : 'not reached'
                }`}
              />
              <div
                className={`mt-1.5 text-center text-2xs tabular ${
                  current ? 'font-semibold text-ink' : 'text-ink-faint'
                }`}
              >
                {weight}%
              </div>
            </li>
          )
        })}
      </ol>
      <p className="mt-3 text-sm text-ink-muted">
        {aborted
          ? `Aborted at ${deployment.current_weight}% and returned to the previous release.`
          : `Currently routing ${deployment.current_weight}% of traffic to the candidate.`}
      </p>
    </div>
  )
}

function AssessmentCard({
  title, assessment,
}: {
  title: string
  assessment: HealthAssessment | undefined
}) {
  if (!assessment) {
    return (
      <div className="card card-pad">
        <div className="label mb-2">{title}</div>
        <p className="text-sm text-ink-muted">No measurement recorded.</p>
      </div>
    )
  }
  return (
    <div className="card card-pad">
      <div className="mb-2 flex items-center justify-between">
        <span className="label">{title}</span>
        <Tone tone={verdictTone(assessment.verdict)}>{assessment.verdict}</Tone>
      </div>
      <dl className="grid grid-cols-3 gap-3">
        <Field label="Requests">
          <span className="tabular">{assessment.requests}</span>
        </Field>
        <Field label="Errors">
          <span className="tabular">{assessment.errors}</span>
        </Field>
        <Field label="Error rate">
          <span className="tabular">{formatPercent(assessment.error_rate, 2)}</span>
        </Field>
      </dl>
      {assessment.latency_p95_ms !== null && (
        <p className="mt-2 text-2xs text-ink-faint tabular">
          p50 {assessment.latency_p50_ms?.toFixed(1) ?? '—'}ms · p95{' '}
          {assessment.latency_p95_ms.toFixed(1)}ms · p99{' '}
          {assessment.latency_p99_ms?.toFixed(1) ?? '—'}ms
        </p>
      )}
      <p className="mt-2 text-sm text-ink-muted">{assessment.reason}</p>
      {assessment.breaches.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {assessment.breaches.map((breach) => (
            <li key={breach}>
              <Tone tone="danger">{titleCase(breach)}</Tone>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EventRow({ event }: { event: DeploymentEvent }) {
  const dangerous = event.kind.includes('rollback') || event.kind === 'deployment_failed'
  const good = event.kind === 'promoted' || event.kind === 'candidate_verified'
  const colour = dangerous ? 'bg-danger' : good ? 'bg-ok' : 'bg-ink-faint'
  return (
    <li className="relative flex gap-3 px-4 py-2.5">
      <span
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${colour}`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-sm font-medium text-ink">{titleCase(event.kind)}</span>
          {event.from_state && event.to_state && (
            <span className="mono text-ink-faint">
              {event.from_state} → {event.to_state}
            </span>
          )}
          <span className="ml-auto text-2xs text-ink-faint">
            {formatRelative(event.created_at)}
          </span>
        </div>
        <p className="mt-0.5 text-sm text-ink-muted">{event.message}</p>
        <p className="mt-0.5 text-2xs text-ink-faint">by {event.actor}</p>
      </div>
    </li>
  )
}

export function DeploymentDetailPage() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const { data, isLoading, error, refetch } = useDeployment(id)
  const cancel = useCancelDeployment()
  const rollback = useRollbackDeployment()

  if (isLoading && !data) return <Loading />
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const meta = deploymentMeta(data.state)
  const rolledBack = data.state === 'ROLLED_BACK'
  const rollbackFailed = data.state === 'ROLLBACK_FAILED'
  const summary = data.health_summary

  return (
    <div>
      <PageHeader
        title={`${data.service_name || 'Deployment'} #${data.deployment_number}`}
        description={`${data.version || 'unknown version'} to ${
          data.environment_name || 'unknown environment'
        }`}
        actions={
          <>
            <StateBadge meta={meta} />
            {meta.active && can('deployment:cancel') && (
              <button
                type="button"
                className="btn-danger"
                disabled={cancel.isPending || data.cancel_requested}
                onClick={() => cancel.mutate(id)}
              >
                {data.cancel_requested ? 'Cancelling…' : 'Cancel'}
              </button>
            )}
            {!meta.active && data.previous_artifact_id && can('deployment:rollback') && (
              <button
                type="button"
                className="btn-ghost"
                disabled={rollback.isPending}
                onClick={() => {
                  if (window.confirm('Roll back to the previous artifact?')) {
                    rollback.mutate(id)
                  }
                }}
              >
                Roll back
              </button>
            )}
          </>
        }
      />

      {(rolledBack || rollbackFailed) && (
        <div
          role="alert"
          className={`card card-pad mb-4 ${
            rollbackFailed
              ? 'border-danger/40 bg-danger-soft/50'
              : 'border-warn/30 bg-warn-soft/40'
          }`}
        >
          <h2
            className={`text-sm font-medium ${
              rollbackFailed ? 'text-danger' : 'text-warn'
            }`}
          >
            {rollbackFailed
              ? 'Rollback could not be verified — manual intervention required'
              : 'Automatic rollback completed and verified'}
          </h2>
          <p className="mt-1 text-sm text-ink-muted">{data.rollback_reason}</p>
          <p className="mt-2 text-2xs text-ink-faint">
            Recovery verification:{' '}
            {data.rollback_verified
              ? 'the previous release was confirmed serving traffic by live HTTP checks.'
              : 'Forge could not confirm the previous release is serving.'}
            {data.previous_version && ` Restored version: ${data.previous_version}.`}
          </p>
        </div>
      )}

      {data.failure_reason && !rolledBack && !rollbackFailed && (
        <div role="alert" className="card card-pad mb-4 border-danger/30 bg-danger-soft/40">
          <h2 className="text-sm font-medium text-danger">Deployment failed</h2>
          <p className="mt-1 text-sm text-ink-muted">{data.failure_reason}</p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <CanaryProgress deployment={data} />

          <div className="grid gap-4 sm:grid-cols-2">
            <AssessmentCard title="Candidate health" assessment={summary.candidate} />
            <AssessmentCard title="Stable health" assessment={summary.stable} />
          </div>

          <section className="card" aria-labelledby="metrics-heading">
            <div className="border-b border-surface-border px-4 py-3">
              <h2 id="metrics-heading" className="text-sm font-medium text-ink">
                Measured health over the rollout
              </h2>
            </div>
            <HealthChart samples={data.health_samples} threshold={0.05} />
          </section>

          <section className="card" aria-labelledby="timeline-heading">
            <div className="border-b border-surface-border px-4 py-3">
              <h2 id="timeline-heading" className="text-sm font-medium text-ink">
                Deployment timeline
              </h2>
            </div>
            {data.events.length === 0 ? (
              <EmptyState title="No events recorded" icon="☰" />
            ) : (
              <ol className="divide-y divide-surface-border">
                {data.events.map((event) => (
                  <EventRow key={event.id} event={event} />
                ))}
              </ol>
            )}
          </section>
        </div>

        <div className="space-y-4">
          <section className="card card-pad" aria-labelledby="release-heading">
            <h2 id="release-heading" className="label mb-3">
              Release
            </h2>
            <dl className="space-y-3">
              <Field label="Service">{data.service_name || '—'}</Field>
              <Field label="Environment">{data.environment_name || '—'}</Field>
              <Field label="Version">{data.version || '—'}</Field>
              <Field label="Artifact digest">
                {data.artifact_digest ? <Digest value={data.artifact_digest} /> : '—'}
              </Field>
              <Field label="Previous version">{data.previous_version || '—'}</Field>
              <Field label="Strategy">{data.strategy}</Field>
              <Field label="Duration">{formatDuration(data.duration_ms)}</Field>
              <Field label="Started">{formatAbsolute(data.started_at)}</Field>
            </dl>
          </section>

          <section className="card card-pad">
            <h2 className="label mb-2">Trace</h2>
            <p className="text-2xs leading-relaxed text-ink-faint">
              This deployment references an immutable artifact digest, which records
              the repository, commit and pipeline run that produced it.
            </p>
            <Link to="/security" className="mt-2 inline-block text-2xs text-accent
                                            hover:underline">
              View artifact provenance and SBOM →
            </Link>
          </section>
        </div>
      </div>
    </div>
  )
}
