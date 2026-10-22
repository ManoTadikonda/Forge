/** Small shared presentational building blocks. */

import type { ReactNode } from 'react'
import { TONE_CLASSES, TONE_DOT, type StateMeta, type Tone } from '@/lib/state'

export function StateBadge({ meta }: { meta: StateMeta }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5
                  text-2xs font-medium ${TONE_CLASSES[meta.tone]}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[meta.tone]} ${
          meta.active ? 'animate-pulse-soft' : ''
        }`}
        aria-hidden="true"
      />
      {meta.label}
    </span>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <span role="status" aria-live="polite" className="inline-flex items-center gap-2">
      <span
        className="h-3.5 w-3.5 animate-spin rounded-full border-2
                   border-ink-faint border-t-accent"
        aria-hidden="true"
      />
      <span className="sr-only">{label}</span>
    </span>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-12 text-sm text-ink-muted">
      <Spinner />
      <span>{label}</span>
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
  icon = '○',
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: string
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div
        className="mb-3 flex h-10 w-10 items-center justify-center rounded-full
                   border border-surface-border text-lg text-ink-faint"
        aria-hidden="true"
      >
        {icon}
      </div>
      <h3 className="text-sm font-medium text-ink">{title}</h3>
      {description && (
        <p className="mt-1.5 max-w-sm text-sm text-ink-muted">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown
  onRetry?: () => void
}) {
  const message =
    error instanceof Error ? error.message : 'Something went wrong.'
  // Only surface a request id we can actually show; anything else is noise.
  const candidate =
    error && typeof error === 'object' && 'requestId' in error
      ? (error as Record<'requestId', unknown>).requestId
      : undefined
  const requestId = typeof candidate === 'string' ? candidate : ''
  return (
    <div role="alert" className="card card-pad border-danger/30 bg-danger-soft/40">
      <h3 className="text-sm font-medium text-danger">Could not load this view</h3>
      <p className="mt-1.5 text-sm text-ink-muted">{message}</p>
      {requestId && (
        <p className="mt-2 mono text-ink-faint">request id: {requestId}</p>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-ghost mt-3">
          Try again
        </button>
      )}
    </div>
  )
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && (
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="mt-1 text-sm text-ink">{children}</dd>
    </div>
  )
}

export function Digest({ value, full = false }: { value: string; full?: boolean }) {
  const display = full ? value : `${value.slice(0, 19)}…`
  return (
    <code className="mono rounded bg-surface px-1.5 py-0.5 text-ink-muted" title={value}>
      {display}
    </code>
  )
}

export function Tone({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`rounded border px-1.5 py-0.5 text-2xs ${TONE_CLASSES[tone]}`}>
      {children}
    </span>
  )
}
