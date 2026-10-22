import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useArtifacts, useIncidents } from '@/hooks/queries'
import { api } from '@/lib/api'
import {
  Digest, EmptyState, ErrorState, Loading, PageHeader, Tone,
} from '@/components/primitives'
import { Pagination, Table } from '@/components/Table'
import { formatBytes, formatRelative, shortSha } from '@/lib/format'

const LIMIT = 20

export function SecurityPage() {
  const [offset, setOffset] = useState(0)
  const { data, isLoading, error, refetch } = useArtifacts({ limit: LIMIT, offset })
  const { data: incidents } = useIncidents()

  return (
    <div>
      <PageHeader
        title="Security & supply chain"
        description="Artifact provenance, SBOMs, scan results and recorded incidents."
      />

      <section className="card mb-4" aria-labelledby="incidents-heading">
        <div className="border-b border-surface-border px-4 py-3">
          <h2 id="incidents-heading" className="text-sm font-medium text-ink">
            Incidents
          </h2>
        </div>
        {!incidents || incidents.length === 0 ? (
          <EmptyState
            icon="⛉"
            title="No incidents recorded"
            description="A rollback or failed release creates an incident here."
          />
        ) : (
          <ul className="divide-y divide-surface-border">
            {incidents.map((incident) => (
              <li key={incident.id} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Tone tone={incident.severity === 'CRITICAL' ? 'danger' : 'warn'}>
                    {incident.severity}
                  </Tone>
                  <span className="text-sm font-medium text-ink">{incident.title}</span>
                  <span className="ml-auto text-2xs text-ink-faint">
                    {formatRelative(incident.created_at)}
                  </span>
                </div>
                <p className="mt-1 whitespace-pre-wrap text-sm text-ink-muted">
                  {incident.detail}
                </p>
                {incident.deployment_id && (
                  <Link
                    to={`/deployments/${incident.deployment_id}`}
                    className="mt-1.5 inline-block text-2xs text-accent hover:underline"
                  >
                    View deployment →
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <section className="card" aria-labelledby="artifacts-heading">
          <div className="border-b border-surface-border px-4 py-3">
            <h2 id="artifacts-heading" className="text-sm font-medium text-ink">
              Artifacts
            </h2>
            <p className="mt-0.5 text-2xs text-ink-faint">
              Every artifact is addressed by an immutable digest and records the
              repository, commit and pipeline run that produced it.
            </p>
          </div>

          {isLoading && !data ? (
            <Loading />
          ) : !data || data.items.length === 0 ? (
            <EmptyState
              icon="⛉"
              title="No artifacts built yet"
              description="Run a pipeline to produce one."
            />
          ) : (
            <>
              <Table
                caption="Built artifacts"
                columns={[
                  'Digest', 'Version', 'Commit', 'Size', 'Signed', 'Scan', 'SBOM',
                ]}
              >
                {data.items.map((artifact) => (
                  <tr key={artifact.id}>
                    <td className="td">
                      <Digest value={artifact.digest} />
                      <div className="mt-0.5 text-2xs text-ink-faint">
                        {artifact.image_name} · {artifact.builder}
                      </div>
                    </td>
                    <td className="td">{artifact.version}</td>
                    <td className="td mono">
                      {artifact.pipeline_run_id ? (
                        <Link
                          to={`/pipelines/${artifact.pipeline_run_id}`}
                          className="text-accent hover:underline"
                        >
                          {shortSha(artifact.commit_sha)}
                        </Link>
                      ) : (
                        shortSha(artifact.commit_sha)
                      )}
                    </td>
                    <td className="td tabular">{formatBytes(artifact.size_bytes)}</td>
                    <td className="td">
                      {artifact.signed ? (
                        <Tone tone="ok">Signed</Tone>
                      ) : (
                        /* Honest: unsigned because signing is not configured,
                           not because the artifact failed verification. */
                        <Tone tone="neutral">Not configured</Tone>
                      )}
                    </td>
                    <td className="td">
                      {artifact.scan_status === 'PASSED' ? (
                        <Tone tone="ok">Passed</Tone>
                      ) : artifact.scan_status === 'FINDINGS' ? (
                        <Tone tone="warn">Findings</Tone>
                      ) : (
                        <span title={artifact.scan_skip_reason}>
                          <Tone tone="warn">Skipped</Tone>
                        </span>
                      )}
                    </td>
                    <td className="td">
                      {artifact.sbom_component_count > 0 ? (
                        <a
                          href={api.sbomUrl(artifact.id)}
                          className="text-accent hover:underline"
                          download
                        >
                          {artifact.sbom_component_count} components
                        </a>
                      ) : (
                        <span className="text-ink-faint">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
              <Pagination
                total={data.total}
                limit={LIMIT}
                offset={offset}
                onChange={setOffset}
              />
            </>
          )}
        </section>
      )}

      {data && data.items.some((artifact) => artifact.scan_status === 'SKIPPED') && (
        <div className="card card-pad mt-4 border-warn/30 bg-warn-soft/30">
          <h3 className="text-sm font-medium text-warn">Scanning is not enabled</h3>
          <p className="mt-1 text-sm text-ink-muted">
            {data.items.find((artifact) => artifact.scan_skip_reason)?.scan_skip_reason}
          </p>
          <p className="mt-1.5 text-2xs text-ink-faint">
            Skipped gates are recorded as skipped with a reason. They are never
            reported as passing.
          </p>
        </div>
      )}
    </div>
  )
}
