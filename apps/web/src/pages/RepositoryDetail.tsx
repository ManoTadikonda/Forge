import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useRepository, useTriggerPipeline } from '@/hooks/queries'
import { useAuth } from '@/lib/auth'
import {
  EmptyState, ErrorState, Field, Loading, PageHeader, Spinner, StateBadge,
} from '@/components/primitives'
import { Table } from '@/components/Table'
import { pipelineMeta } from '@/lib/state'
import { formatAbsolute, formatDuration, formatRelative, shortSha } from '@/lib/format'

export function RepositoryDetailPage() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const { data, isLoading, error, refetch } = useRepository(id)
  const trigger = useTriggerPipeline()
  const [message, setMessage] = useState('')

  if (isLoading && !data) return <Loading />
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return null

  return (
    <div>
      <PageHeader
        title={data.name}
        description={data.description || data.slug}
        actions={
          can('pipeline:trigger') && (
            <button
              type="button"
              className="btn-primary"
              disabled={trigger.isPending || !data.is_active}
              onClick={async () => {
                setMessage('')
                try {
                  await trigger.mutateAsync({ repository_id: data.id })
                  setMessage('Pipeline queued.')
                } catch (caught) {
                  setMessage(
                    caught instanceof Error ? caught.message : 'Could not trigger a run.',
                  )
                }
              }}
            >
              {trigger.isPending ? <Spinner label="Triggering" /> : 'Run pipeline'}
            </button>
          )
        }
      />

      {!data.is_active && (
        <div className="card card-pad mb-4 border-warn/30 bg-warn-soft/40">
          <p className="text-sm text-warn">
            This repository has been removed. Its history is retained for audit, but
            new pipelines cannot be triggered.
          </p>
        </div>
      )}

      {message && (
        <p role="status" className="card card-pad mb-4 text-sm text-ink-muted">
          {message}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <section className="card card-pad lg:col-span-1" aria-labelledby="repo-heading">
          <h2 id="repo-heading" className="label mb-3">
            Repository
          </h2>
          <dl className="space-y-3">
            <Field label="URL">
              <span className="mono break-all text-ink-muted">{data.url}</span>
            </Field>
            <Field label="Default branch">{data.default_branch}</Field>
            <Field label="Pipeline runs">{data.pipeline_count}</Field>
            <Field label="Webhook">
              {data.webhook_enabled ? 'Enabled' : 'Not configured'}
            </Field>
            <Field label="Last pipeline">{formatRelative(data.last_pipeline_at)}</Field>
            <Field label="Registered">{formatAbsolute(data.created_at)}</Field>
          </dl>
        </section>

        <section className="card lg:col-span-2" aria-labelledby="runs-heading">
          <div className="flex items-center justify-between border-b
                          border-surface-border px-4 py-3">
            <h2 id="runs-heading" className="text-sm font-medium text-ink">
              Recent pipeline runs
            </h2>
            <Link to="/pipelines" className="text-2xs text-accent hover:underline">
              View all
            </Link>
          </div>
          {data.recent_pipelines.length === 0 ? (
            <EmptyState
              icon="⚙"
              title="No runs yet"
              description="Trigger a pipeline to build this repository."
            />
          ) : (
            <Table
              caption="Recent pipeline runs"
              columns={['State', 'Run', 'Commit', 'Duration', 'Started']}
            >
              {data.recent_pipelines.map((run) => (
                <tr key={run.id} className="row-link">
                  <td className="td">
                    <Link to={`/pipelines/${run.id}`}>
                      <StateBadge meta={pipelineMeta(run.state)} />
                    </Link>
                  </td>
                  <td className="td">
                    <Link
                      to={`/pipelines/${run.id}`}
                      className="font-medium text-ink hover:text-accent"
                    >
                      #{run.run_number}
                    </Link>
                  </td>
                  <td className="td mono">{shortSha(run.commit_sha)}</td>
                  <td className="td tabular">{formatDuration(run.duration_ms)}</td>
                  <td className="td">{formatRelative(run.created_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </section>
      </div>
    </div>
  )
}
