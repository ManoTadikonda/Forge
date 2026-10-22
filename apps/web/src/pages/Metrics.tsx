import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { useDeployments, useOverview, usePipelines } from '@/hooks/queries'
import {
  EmptyState, ErrorState, Loading, PageHeader,
} from '@/components/primitives'
import { formatDuration, formatPercent } from '@/lib/format'

// Shared theme tokens, same as HealthChart — see index.css.
const AXIS = { stroke: 'rgb(var(--ink-faint))', fontSize: 11 }
const GRID = 'rgb(var(--surface-border))'
const TOOLTIP_STYLE = {
  backgroundColor: 'rgb(var(--surface-raised))',
  border: '1px solid rgb(var(--surface-border))',
  color: 'rgb(var(--ink))',
  borderRadius: 6,
  fontSize: 12,
}

export function MetricsPage() {
  const { data: overview, isLoading, error, refetch } = useOverview()
  const { data: pipelines } = usePipelines({ limit: 50 })
  const { data: deployments } = useDeployments({ limit: 50 })

  if (isLoading && !overview) return <Loading />
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!overview) return null

  const finished = (pipelines?.items ?? []).filter((run) => run.duration_ms !== null)
  // Oldest first, so the chart reads left-to-right in chronological order.
  const durations = [...finished].reverse().map((run, index) => ({
    label: `#${run.run_number}`,
    index,
    duration: Math.round((run.duration_ms ?? 0) / 100) / 10,
    failed: run.state === 'FAILED',
  }))

  const outcomes = [
    {
      name: 'Succeeded',
      value: overview.stats.deployments_succeeded,
      fill: 'rgb(var(--ok))',
    },
    {
      name: 'Rolled back',
      value: overview.stats.deployments_rolled_back,
      fill: 'rgb(var(--warn))',
    },
    {
      name: 'Other',
      value: Math.max(
        0,
        overview.stats.deployments_total -
          overview.stats.deployments_succeeded -
          overview.stats.deployments_rolled_back,
      ),
      fill: 'rgb(var(--ink-faint))',
    },
  ].filter((entry) => entry.value > 0)

  const totalDuration = finished.reduce((sum, run) => sum + (run.duration_ms ?? 0), 0)

  return (
    <div>
      <PageHeader
        title="Metrics"
        description="Measured from recorded executions. The API exposes the full Prometheus
                     exposition separately, on its own /metrics endpoint."
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="card card-pad">
          <div className="label">Pipeline success rate</div>
          <div className="mt-1.5 text-2xl font-semibold tabular text-ink">
            {formatPercent(overview.stats.pipeline_success_rate)}
          </div>
        </div>
        <div className="card card-pad">
          <div className="label">Mean build duration</div>
          <div className="mt-1.5 text-2xl font-semibold tabular text-ink">
            {finished.length === 0
              ? '—'
              : formatDuration(totalDuration / finished.length)}
          </div>
          <div className="mt-1 text-2xs text-ink-faint">
            over {finished.length} finished runs
          </div>
        </div>
        <div className="card card-pad">
          <div className="label">Rollback rate</div>
          <div className="mt-1.5 text-2xl font-semibold tabular text-ink">
            {overview.stats.deployments_total === 0
              ? '—'
              : formatPercent(
                  overview.stats.deployments_rolled_back /
                    overview.stats.deployments_total,
                )}
          </div>
        </div>
        <div className="card card-pad">
          <div className="label">Artifacts built</div>
          <div className="mt-1.5 text-2xl font-semibold tabular text-ink">
            {overview.stats.artifacts}
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <section className="card" aria-labelledby="duration-heading">
          <div className="border-b border-surface-border px-4 py-3">
            <h2 id="duration-heading" className="text-sm font-medium text-ink">
              Build duration by run (seconds)
            </h2>
          </div>
          {durations.length === 0 ? (
            <EmptyState
              icon="◭"
              title="No finished runs yet"
              description="This chart plots real execution times only."
            />
          ) : (
            <div className="p-4">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={durations} margin={{ top: 4, right: 8, left: -16 }}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                  <XAxis dataKey="label" {...AXIS} />
                  <YAxis {...AXIS} />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value: number) => [`${value}s`, 'Duration']}
                  />
                  <Bar dataKey="duration" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                    {durations.map((entry) => (
                      <Cell
                        key={entry.index}
                        fill={
                          entry.failed
                            ? 'rgb(var(--danger))'
                            : 'rgb(var(--accent))'
                        }
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <p className="mt-2 text-2xs text-ink-faint">
                Red bars are failed runs.
              </p>
            </div>
          )}
        </section>

        <section className="card" aria-labelledby="outcomes-heading">
          <div className="border-b border-surface-border px-4 py-3">
            <h2 id="outcomes-heading" className="text-sm font-medium text-ink">
              Deployment outcomes
            </h2>
          </div>
          {outcomes.length === 0 ? (
            <EmptyState icon="⇪" title="No deployments yet" />
          ) : (
            <div className="p-4">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart
                  data={outcomes}
                  layout="vertical"
                  margin={{ top: 4, right: 16, left: 24 }}
                >
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                  <XAxis type="number" {...AXIS} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" width={88} {...AXIS} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Bar dataKey="value" radius={[0, 3, 3, 0]} isAnimationActive={false}>
                    {outcomes.map((entry) => (
                      <Cell key={entry.name} fill={entry.fill} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>
      </div>

      <section className="card mt-4" aria-labelledby="queue-heading">
        <div className="border-b border-surface-border px-4 py-3">
          <h2 id="queue-heading" className="text-sm font-medium text-ink">
            Job queue depth
          </h2>
        </div>
        <div className="grid grid-cols-2 gap-px bg-surface-border sm:grid-cols-3
                        lg:grid-cols-6">
          {Object.entries(overview.stats.queue_depth).map(([state, count]) => (
            <div key={state} className="bg-surface-raised px-4 py-3">
              <div className="label">{state}</div>
              <div className="mt-1 text-lg font-semibold tabular text-ink">{count}</div>
            </div>
          ))}
        </div>
      </section>

      <p className="mt-4 text-2xs text-ink-faint">
        {deployments?.total ?? 0} deployments recorded. Scrape the API's{' '}
        <code className="mono">/metrics</code> endpoint for the full Prometheus
        exposition, including queue depth, job durations, HTTP latency histograms
        and rollback counters.
      </p>
    </div>
  )
}
