import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useDeployments } from '@/hooks/queries'
import {
  EmptyState, ErrorState, Loading, PageHeader, StateBadge,
} from '@/components/primitives'
import { Filters, Pagination, Table } from '@/components/Table'
import { deploymentMeta } from '@/lib/state'
import { formatDuration, formatRelative } from '@/lib/format'
import type { DeploymentState } from '@/lib/types'

const LIMIT = 25
const STATES: (DeploymentState | '')[] = [
  '', 'PENDING', 'PREPARING', 'DEPLOYING', 'VERIFYING', 'CANARY', 'PROMOTING',
  'SUCCEEDED', 'FAILED', 'CANCELED', 'ROLLING_BACK', 'ROLLED_BACK',
  'ROLLBACK_FAILED',
]

export function DeploymentsPage() {
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<DeploymentState | ''>('')
  const { data, isLoading, error, refetch } = useDeployments({
    limit: LIMIT, offset, state,
  })

  return (
    <div>
      <PageHeader
        title="Deployments"
        description="Progressive rollouts, their health decisions and rollback outcomes."
      />

      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="card">
          <Filters>
            <label htmlFor="deploy-state" className="label">
              State
            </label>
            <select
              id="deploy-state"
              className="input max-w-[14rem]"
              value={state}
              onChange={(event) => {
                setState(event.target.value as DeploymentState | '')
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
              icon="⇪"
              title={state ? `No ${state} deployments` : 'No deployments yet'}
              description={
                state
                  ? 'Try a different state filter.'
                  : 'Deploy a built artifact to see rollouts here.'
              }
            />
          ) : (
            <>
              <Table
                caption="Deployments"
                columns={['State', 'Deployment', 'Traffic', 'Verified', 'Duration', 'Created']}
              >
                {data.items.map((deployment) => {
                  const meta = deploymentMeta(deployment.state)
                  const rolledBack = deployment.state.startsWith('ROLL')
                  return (
                    <tr key={deployment.id} className="row-link">
                      <td className="td">
                        <Link to={`/deployments/${deployment.id}`}>
                          <StateBadge meta={meta} />
                        </Link>
                      </td>
                      <td className="td">
                        <Link
                          to={`/deployments/${deployment.id}`}
                          className="font-medium text-ink hover:text-accent"
                        >
                          #{deployment.deployment_number}
                        </Link>
                        <div className="mt-0.5 text-2xs text-ink-faint">
                          {deployment.strategy}
                        </div>
                      </td>
                      <td className="td tabular">{deployment.current_weight}%</td>
                      <td className="td">
                        {rolledBack ? (
                          deployment.rollback_verified ? (
                            <span className="text-ok text-2xs">Recovery verified</span>
                          ) : (
                            <span className="text-danger text-2xs">Not verified</span>
                          )
                        ) : (
                          <span className="text-ink-faint">—</span>
                        )}
                      </td>
                      <td className="td tabular">
                        {formatDuration(deployment.duration_ms)}
                      </td>
                      <td className="td">{formatRelative(deployment.created_at)}</td>
                    </tr>
                  )
                })}
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
