import { useState } from 'react'
import { useAudit } from '@/hooks/queries'
import {
  EmptyState, ErrorState, Loading, PageHeader, Tone,
} from '@/components/primitives'
import { Filters, Pagination, Table } from '@/components/Table'
import { formatAbsolute, formatRelative } from '@/lib/format'
import type { AuditEvent } from '@/lib/types'

const LIMIT = 50
const OUTCOMES = ['', 'SUCCESS', 'FAILURE', 'DENIED'] as const

function outcomeTone(outcome: AuditEvent['outcome']) {
  if (outcome === 'SUCCESS') return 'ok' as const
  if (outcome === 'DENIED') return 'warn' as const
  return 'danger' as const
}

export function AuditPage() {
  const [offset, setOffset] = useState(0)
  const [outcome, setOutcome] = useState('')
  const [action, setAction] = useState('')
  const { data, isLoading, error, refetch } = useAudit({
    limit: LIMIT, offset, outcome, action,
  })

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="Append-only record of security-sensitive operations."
      />

      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="card">
          <Filters>
            <label htmlFor="audit-outcome" className="label">
              Outcome
            </label>
            <select
              id="audit-outcome"
              className="input max-w-[10rem]"
              value={outcome}
              onChange={(event) => {
                setOutcome(event.target.value)
                setOffset(0)
              }}
            >
              {OUTCOMES.map((value) => (
                <option key={value || 'all'} value={value}>
                  {value || 'All outcomes'}
                </option>
              ))}
            </select>
            <label htmlFor="audit-action" className="label ml-2">
              Action
            </label>
            <input
              id="audit-action"
              type="search"
              placeholder="e.g. login, deployment.create"
              className="input max-w-xs"
              value={action}
              onChange={(event) => {
                setAction(event.target.value)
                setOffset(0)
              }}
            />
          </Filters>

          {isLoading && !data ? (
            <Loading />
          ) : !data || data.items.length === 0 ? (
            <EmptyState
              icon="☰"
              title="No audit events match"
              description="Sign-ins, repository changes and deployments are recorded here."
            />
          ) : (
            <>
              <Table
                caption="Audit events"
                columns={['When', 'Actor', 'Action', 'Resource', 'Outcome', 'Source']}
              >
                {data.items.map((event) => (
                  <tr key={event.id}>
                    <td className="td whitespace-nowrap" title={formatAbsolute(event.created_at)}>
                      {formatRelative(event.created_at)}
                    </td>
                    <td className="td">{event.actor_label}</td>
                    <td className="td mono">{event.action}</td>
                    <td className="td">
                      {event.resource_type ? (
                        <>
                          <span className="text-ink-muted">{event.resource_type}</span>
                          <div className="mono mt-0.5 text-ink-faint">
                            {event.resource_id.slice(0, 8)}
                          </div>
                        </>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="td">
                      <Tone tone={outcomeTone(event.outcome)}>{event.outcome}</Tone>
                    </td>
                    <td className="td mono text-ink-faint">
                      {event.ip_address || '—'}
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
        </div>
      )}
    </div>
  )
}
