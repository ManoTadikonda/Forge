/** Accessible table + pagination shared across list pages. */

import type { ReactNode } from 'react'

export function Table({
  columns, children, caption,
}: {
  columns: string[]
  children: ReactNode
  caption?: string
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="border-b border-surface-border bg-surface/40">
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col" className="th">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-surface-border">{children}</tbody>
      </table>
    </div>
  )
}

export function Pagination({
  total, limit, offset, onChange,
}: {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
}) {
  if (total <= limit) return null
  const page = Math.floor(offset / limit) + 1
  const pages = Math.ceil(total / limit)
  const first = total === 0 ? 0 : offset + 1
  const last = Math.min(offset + limit, total)

  return (
    <nav
      aria-label="Pagination"
      className="flex items-center justify-between border-t border-surface-border
                 px-4 py-2.5"
    >
      <p className="text-2xs text-ink-faint tabular">
        {first}–{last} of {total}
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-ghost px-2 py-1 text-2xs"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </button>
        <span className="text-2xs text-ink-faint tabular">
          {page} / {pages}
        </span>
        <button
          type="button"
          className="btn-ghost px-2 py-1 text-2xs"
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </button>
      </div>
    </nav>
  )
}

export function Filters({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-surface-border
                    px-4 py-2.5">
      {children}
    </div>
  )
}
