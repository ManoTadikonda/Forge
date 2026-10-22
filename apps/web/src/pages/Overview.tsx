import { Link } from 'react-router-dom'
import { useOverview } from '@/hooks/queries'
import {
  EmptyState, ErrorState, Loading, PageHeader, StateBadge,
} from '@/components/primitives'
import { deploymentMeta, pipelineMeta } from '@/lib/state'
import { formatDuration, formatPercent, formatRelative, shortSha } from '@/lib/format'
import type { OverviewStats } from '@/lib/types'

function Stat({
  label, value, hint, tone = 'neutral',
}: {
  label: string
  value: string
  hint?: string
  tone?: 'neutral' | 'ok' | 'warn' | 'danger'
}) {
  const toneClass = {
    neutral: 'text-ink',
    ok: 'text-ok',
    warn: 'text-warn',
    danger: 'text-danger',
  }[tone]
  return (
    <div className="card card-pad">
      <div className="label">{label}</div>
      <div className={`mt-1.5 text-2xl font-semibold tabular ${toneClass}`}>{value}</div>
      {hint && <div className="mt-1 text-2xs text-ink-faint">{hint}</div>}
    </div>
  )
}

function QueueDepth({ depth }: { depth: Record<string, number> }) {
  const entries = Object.entries(depth).filter(([, count]) => count > 0)
  return (
    <div className="card card-pad">
      <div className="label">Job queue</div>
      {entries.length === 0 ? (
        <p className="mt-2 text-sm text-ink-muted">Queue is empty.</p>
      ) : (
        <dl className="mt-2 space-y-1.5">
          {entries.map(([state, count]) => (
            <div key={state} className="flex items-center justify-between text-sm">
              <dt className="text-ink-muted">{state}</dt>
              <dd className="tabular font-medium text-ink">{count}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

export function OverviewPage() {
  const { data, isLoading, error, refetch } = useOverview()

  if (isLoading) return <Loading />
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const stats: OverviewStats = data.stats
  const nothingHasRun = stats.pipelines_total === 0 && stats.deployments_total === 0

  return (
    <div>
      <PageHeader
        title="Overview"
        description="Live state of every repository, pipeline and deployment Forge manages."
      />

      {nothingHasRun ? (
        <div className="card">
          <EmptyState
            icon="◳"
            title="Nothing has run yet"
            description="Register a repository and trigger a pipeline. This page shows real
                         execution results only — it never displays sample data."
            action={
              <Link to="/repositories" className="btn-primary">
                Register a repository
              </Link>
            }
          />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat
              label="Pipeline success rate"
              // null means nothing has finished; showing 0% would be a lie.
              value={formatPercent(stats.pipeline_success_rate)}
              hint={
                stats.pipeline_success_rate === null
                  ? 'No finished runs yet'
                  : `${stats.pipelines_succeeded} of ${
                      stats.pipelines_succeeded + stats.pipelines_failed
                    } finished runs`
              }
              tone={
                stats.pipeline_success_rate === null
                  ? 'neutral'
                  : stats.pipeline_success_rate >= 0.9
                    ? 'ok'
                    : 'warn'
              }
            />
            <Stat
              label="Deployments"
              value={String(stats.deployments_total)}
              hint={`${stats.deployments_succeeded} succeeded, ${stats.deployments_rolled_back} rolled back`}
            />
            <Stat
              label="In flight"
              value={String(stats.pipelines_running + stats.deployments_active)}
              hint={`${stats.pipelines_running} pipelines, ${stats.deployments_active} deployments`}
              tone={stats.pipelines_running + stats.deployments_active > 0 ? 'warn' : 'neutral'}
            />
            <Stat
              label="Open incidents"
              value={String(stats.open_incidents)}
              hint={stats.open_incidents > 0 ? 'Needs attention' : 'All clear'}
              tone={stats.open_incidents > 0 ? 'danger' : 'ok'}
            />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <div className="card lg:col-span-2">
              <div className="flex items-center justify-between border-b
                              border-surface-border px-4 py-3">
                <h2 className="text-sm font-medium text-ink">Recent pipelines</h2>
                <Link to="/pipelines" className="text-2xs text-accent hover:underline">
                  View all
                </Link>
              </div>
              {data.recent_pipelines.length === 0 ? (
                <EmptyState title="No pipeline runs yet" icon="⚙" />
              ) : (
                <ul className="divide-y divide-surface-border">
                  {data.recent_pipelines.map((run) => (
                    <li key={run.id}>
                      <Link
                        to={`/pipelines/${run.id}`}
                        className="row-link flex items-center gap-3 px-4 py-2.5"
                      >
                        <StateBadge meta={pipelineMeta(run.state)} />
                        <span className="min-w-0 flex-1 truncate text-sm text-ink">
                          {run.repository_label || 'Unknown repository'}
                          <span className="ml-1.5 text-ink-faint">#{run.run_number}</span>
                        </span>
                        <code className="mono hidden text-ink-faint sm:inline">
                          {shortSha(run.commit_sha)}
                        </code>
                        <span className="tabular w-16 text-right text-2xs text-ink-faint">
                          {formatDuration(run.duration_ms)}
                        </span>
                        <span className="w-16 text-right text-2xs text-ink-faint">
                          {formatRelative(run.created_at)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="space-y-4">
              <QueueDepth depth={stats.queue_depth} />
              <div className="card card-pad">
                <div className="label">Artifacts</div>
                <div className="mt-1.5 text-2xl font-semibold tabular text-ink">
                  {stats.artifacts}
                </div>
                <div className="mt-1 text-2xs text-ink-faint">
                  Immutable, digest-addressed
                </div>
              </div>
              <div className="card card-pad">
                <div className="label">Repositories</div>
                <div className="mt-1.5 text-2xl font-semibold tabular text-ink">
                  {stats.repositories}
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="card">
              <div className="flex items-center justify-between border-b
                              border-surface-border px-4 py-3">
                <h2 className="text-sm font-medium text-ink">Recent deployments</h2>
                <Link to="/deployments" className="text-2xs text-accent hover:underline">
                  View all
                </Link>
              </div>
              {data.recent_deployments.length === 0 ? (
                <EmptyState title="No deployments yet" icon="⇪" />
              ) : (
                <ul className="divide-y divide-surface-border">
                  {data.recent_deployments.map((deployment) => (
                    <li key={deployment.id}>
                      <Link
                        to={`/deployments/${deployment.id}`}
                        className="row-link flex items-center gap-3 px-4 py-2.5"
                      >
                        <StateBadge meta={deploymentMeta(deployment.state)} />
                        <span className="flex-1 truncate text-sm text-ink">
                          Deployment #{deployment.deployment_number}
                        </span>
                        <span className="tabular text-2xs text-ink-faint">
                          {deployment.current_weight}%
                        </span>
                        <span className="w-16 text-right text-2xs text-ink-faint">
                          {formatRelative(deployment.created_at)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="card">
              <div className="border-b border-surface-border px-4 py-3">
                <h2 className="text-sm font-medium text-ink">Incidents</h2>
              </div>
              {data.recent_incidents.length === 0 ? (
                <EmptyState title="No incidents recorded" icon="⛉" />
              ) : (
                <ul className="divide-y divide-surface-border">
                  {data.recent_incidents.map((incident) => (
                    <li key={incident.id} className="px-4 py-3">
                      <div className="flex items-start gap-2">
                        <span className="mt-0.5 text-danger" aria-hidden="true">●</span>
                        <div className="min-w-0">
                          <div className="text-sm text-ink">{incident.title}</div>
                          <div className="mt-0.5 text-2xs text-ink-faint">
                            {incident.severity} · {formatRelative(incident.created_at)}
                          </div>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
