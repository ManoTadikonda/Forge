import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  useCancelPipeline, useCreateDeployment, usePipeline, usePipelineLogs,
} from '@/hooks/queries'
import { useAuth } from '@/lib/auth'
import {
  Digest, EmptyState, ErrorState, Field, Loading, PageHeader, Spinner, StateBadge,
} from '@/components/primitives'
import { pipelineMeta, stageMeta } from '@/lib/state'
import {
  formatAbsolute, formatBytes, formatDuration, formatRelative, shortSha,
} from '@/lib/format'
import type { Stage } from '@/lib/types'

function StageTimeline({ stages }: { stages: Stage[] }) {
  return (
    <ol className="divide-y divide-surface-border">
      {stages.map((stage) => {
        const meta = stageMeta(stage.status)
        const note = stage.message || stage.skip_reason
        return (
          <li key={stage.id} className="flex items-start gap-3 px-4 py-3">
            <div className="w-24 shrink-0">
              <StateBadge meta={meta} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="text-sm font-medium text-ink">{stage.name}</span>
                <span className="tabular text-2xs text-ink-faint">
                  {formatDuration(stage.duration_ms)}
                </span>
                {stage.exit_code !== null && stage.exit_code !== 0 && (
                  <span className="mono text-danger">exit {stage.exit_code}</span>
                )}
              </div>
              {note && (
                <p
                  className={`mt-0.5 text-2xs ${
                    stage.status === 'SKIPPED' ? 'text-warn' : 'text-ink-muted'
                  }`}
                >
                  {note}
                </p>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export function PipelineDetailPage() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const { data, isLoading, error, refetch } = usePipeline(id)
  const active = data ? pipelineMeta(data.state).active : false
  const { data: logs } = usePipelineLogs(id, active)
  const cancel = useCancelPipeline()
  const deploy = useCreateDeployment()
  const [deployError, setDeployError] = useState('')
  const [deployed, setDeployed] = useState('')

  if (isLoading && !data) return <Loading />
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const meta = pipelineMeta(data.state)
  const artifact = data.artifacts[0]

  return (
    <div>
      <PageHeader
        title={`${data.repository_label || 'Pipeline'} #${data.run_number}`}
        description={data.commit_message.split('\n')[0] || 'No commit message.'}
        actions={
          <>
            <StateBadge meta={meta} />
            {meta.active && can('pipeline:cancel') && (
              <button
                type="button"
                className="btn-danger"
                disabled={cancel.isPending || data.cancel_requested}
                onClick={() => cancel.mutate(id)}
              >
                {data.cancel_requested ? 'Cancelling…' : 'Cancel'}
              </button>
            )}
          </>
        }
      />

      {data.failure_reason && (
        <div role="alert" className="card card-pad mb-4 border-danger/30 bg-danger-soft/40">
          <h2 className="text-sm font-medium text-danger">Pipeline failed</h2>
          <p className="mt-1 whitespace-pre-wrap text-sm text-ink-muted">
            {data.failure_reason}
          </p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <section className="card" aria-labelledby="stages-heading">
            <div className="border-b border-surface-border px-4 py-3">
              <h2 id="stages-heading" className="text-sm font-medium text-ink">
                Stages
              </h2>
            </div>
            <StageTimeline stages={data.stages} />
          </section>

          <section className="card" aria-labelledby="logs-heading">
            <div className="flex items-center justify-between border-b
                            border-surface-border px-4 py-3">
              <h2 id="logs-heading" className="text-sm font-medium text-ink">
                Build output
              </h2>
              {active && <Spinner label="Streaming logs" />}
            </div>
            {!logs || logs.length === 0 ? (
              <EmptyState
                icon="☰"
                title="No output yet"
                description={
                  active
                    ? 'Output appears here as the build runs.'
                    : 'This run produced no captured output.'
                }
              />
            ) : (
              <pre
                className="max-h-[28rem] overflow-auto bg-surface px-4 py-3 mono
                           leading-relaxed text-ink-muted"
                tabIndex={0}
                aria-label="Build output"
              >
                {logs.map((line) => line.content).join('')}
                {logs.some((line) => line.truncated) && (
                  <span className="text-warn">
                    {'\n[output truncated at the configured size limit]'}
                  </span>
                )}
              </pre>
            )}
          </section>
        </div>

        <div className="space-y-4">
          <section className="card card-pad" aria-labelledby="run-heading">
            <h2 id="run-heading" className="label mb-3">
              Run
            </h2>
            <dl className="space-y-3">
              <Field label="Commit">
                <code className="mono">{shortSha(data.commit_sha, 12)}</code>
              </Field>
              <Field label="Branch">{data.branch}</Field>
              <Field label="Author">{data.commit_author || '—'}</Field>
              <Field label="Trigger">{data.trigger}</Field>
              <Field label="Duration">{formatDuration(data.duration_ms)}</Field>
              <Field label="Started">{formatAbsolute(data.started_at)}</Field>
              {data.repository_id && (
                <Field label="Repository">
                  <Link
                    to={`/repositories/${data.repository_id}`}
                    className="text-accent hover:underline"
                  >
                    {data.repository_label}
                  </Link>
                </Field>
              )}
            </dl>
          </section>

          <section className="card card-pad" aria-labelledby="artifact-heading">
            <h2 id="artifact-heading" className="label mb-3">
              Artifact
            </h2>
            {!artifact ? (
              <p className="text-sm text-ink-muted">
                No artifact was produced by this run.
              </p>
            ) : (
              <>
                <dl className="space-y-3">
                  <Field label="Digest">
                    <Digest value={artifact.digest} />
                  </Field>
                  <Field label="Version">{artifact.version}</Field>
                  <Field label="Size">{formatBytes(artifact.size_bytes)}</Field>
                  <Field label="Builder">{artifact.builder}</Field>
                  <Field label="Signed">
                    {artifact.signed ? (
                      <span className="text-ok">Yes</span>
                    ) : (
                      <span className="text-warn" title="Signing is not configured">
                        No — signing not configured
                      </span>
                    )}
                  </Field>
                  <Field label="SBOM">
                    {artifact.sbom_format
                      ? `${artifact.sbom_format} · ${artifact.sbom_component_count} components`
                      : '—'}
                  </Field>
                </dl>

                {can('deployment:create') && (
                  <div className="mt-4 border-t border-surface-border pt-4">
                    {deployed ? (
                      <Link to={`/deployments/${deployed}`} className="btn-primary w-full">
                        View deployment
                      </Link>
                    ) : (
                      <button
                        type="button"
                        className="btn-primary w-full"
                        disabled={deploy.isPending}
                        onClick={async () => {
                          setDeployError('')
                          try {
                            const created = await deploy.mutateAsync({
                              artifact_digest: artifact.digest,
                              environment: 'development',
                              service_name: artifact.image_name,
                            })
                            setDeployed(created.id)
                          } catch (caught) {
                            setDeployError(
                              caught instanceof Error
                                ? caught.message
                                : 'Deployment could not be created.',
                            )
                          }
                        }}
                      >
                        {deploy.isPending ? (
                          <Spinner label="Creating deployment" />
                        ) : (
                          'Deploy to development'
                        )}
                      </button>
                    )}
                    {deployError && (
                      <p role="alert" className="mt-2 text-2xs text-danger">
                        {deployError}
                      </p>
                    )}
                  </div>
                )}
              </>
            )}
          </section>

          <p className="text-2xs text-ink-faint">
            Created {formatRelative(data.created_at)}
          </p>
        </div>
      </div>
    </div>
  )
}
