import { useState } from 'react'
import { Link } from 'react-router-dom'
import { usePipelines } from '@/hooks/queries'
import {
  EmptyState, ErrorState, Loading, PageHeader, StateBadge,
} from '@/components/primitives'
import { Filters, Pagination, Table } from '@/components/Table'
import { pipelineMeta } from '@/lib/state'
import { formatDuration, formatRelative, shortSha } from '@/lib/format'
import type { PipelineState } from '@/lib/types'

const LIMIT = 25
const STATES: (PipelineState | '')[] = [
  '', 'QUEUED', 'CLONING', 'VALIDATING', 'TESTING', 'BUILDING', 'PUBLISHING',
  'SUCCEEDED', 'FAILED', 'CANCELED',
]

export function PipelinesPage() {
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<PipelineState | ''>('')
  const { data, isLoading, error, refetch } = usePipelines({
    limit: LIMIT, offset, state,
  })

  return (
    <div>
      <PageHeader
        title="Pipelines"
        description="Every build Forge has executed, with its stages, logs and artifact."
      />

      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="card">
          <Filters>
            <label htmlFor="state-filter" className="label">
              State
            </label>
            <select
              id="state-filter"
              className="input max-w-[12rem]"
              value={state}
              onChange={(event) => {
                setState(event.target.value as PipelineState | '')
                setOffset(0)
              }}
            >
              {STATES.map((value) => (
                <option key={value || 'all'} value={value}>
                  {value || 'All states'}
                </option>
              ))}
            </select>
          </Filters>

          {isLoading && !data ? (
            <Loading />
          ) : !data || data.items.length === 0 ? (
            <EmptyState
              icon="⚙"
              title={state ? `No ${state} pipeline runs` : 'No pipeline runs yet'}
              description={
                state
                  ? 'Try a different state filter.'
                  : 'Trigger a run from a repository to see it here.'
              }
            />
          ) : (
            <>
              <Table
                caption="Pipeline runs"
                columns={['State', 'Repository', 'Commit', 'Trigger', 'Duration', 'Started']}
              >
                {data.items.map((run) => (
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
                        {run.repository_label || 'Unknown'}
                        <span className="ml-1.5 text-ink-faint">#{run.run_number}</span>
                      </Link>
                      <div className="mt-0.5 truncate text-2xs text-ink-faint">
                        {run.branch}
                      </div>
                    </td>
                    <td className="td mono">{shortSha(run.commit_sha)}</td>
                    <td className="td text-2xs">{run.trigger}</td>
                    <td className="td tabular">{formatDuration(run.duration_ms)}</td>
                    <td className="td">{formatRelative(run.created_at)}</td>
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
        </div>
      )}
    </div>
  )
}
