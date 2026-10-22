import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  useCreateRepository, useDeleteRepository, useRepositories,
} from '@/hooks/queries'
import { useAuth } from '@/lib/auth'
import {
  EmptyState, ErrorState, Loading, PageHeader, Spinner,
} from '@/components/primitives'
import { Filters, Pagination, Table } from '@/components/Table'
import { formatRelative } from '@/lib/format'

const LIMIT = 25

function RegisterForm({ onDone }: { onDone: () => void }) {
  const create = useCreateRepository()
  const [url, setUrl] = useState('')
  const [branch, setBranch] = useState('main')
  const [error, setError] = useState('')

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
      await create.mutateAsync({ url: url.trim(), default_branch: branch.trim() })
      setUrl('')
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Registration failed.')
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card card-pad mb-4 space-y-3">
      <div className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
        <div>
          <label htmlFor="repo-url" className="label mb-1.5 block">
            Repository URL
          </label>
          <input
            id="repo-url"
            type="url"
            required
            placeholder="https://github.com/owner/repo"
            className="input"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            aria-describedby="repo-url-hint"
          />
        </div>
        <div>
          <label htmlFor="repo-branch" className="label mb-1.5 block">
            Default branch
          </label>
          <input
            id="repo-branch"
            className="input"
            value={branch}
            onChange={(event) => setBranch(event.target.value)}
          />
        </div>
        <button type="submit" disabled={create.isPending} className="btn-primary">
          {create.isPending ? <Spinner label="Registering" /> : 'Register'}
        </button>
      </div>
      <p id="repo-url-hint" className="text-2xs text-ink-faint">
        Public HTTPS repositories only. URLs resolving to loopback, private or
        link-local addresses are rejected.
      </p>
      {error && (
        <p role="alert" className="rounded border border-danger/30 bg-danger-soft
                                   px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  )
}

export function RepositoriesPage() {
  const { can } = useAuth()
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [showForm, setShowForm] = useState(false)
  const { data, isLoading, error, refetch } = useRepositories({
    limit: LIMIT, offset, search,
  })
  const remove = useDeleteRepository()

  return (
    <div>
      <PageHeader
        title="Repositories"
        description="Connected source repositories Forge builds and deploys."
        actions={
          can('repo:write') && (
            <button
              type="button"
              className="btn-primary"
              onClick={() => setShowForm((open) => !open)}
              aria-expanded={showForm}
            >
              {showForm ? 'Cancel' : 'Register repository'}
            </button>
          )
        }
      />

      {showForm && can('repo:write') && (
        <RegisterForm onDone={() => setShowForm(false)} />
      )}

      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="card">
          <Filters>
            <label htmlFor="repo-search" className="sr-only">
              Search repositories
            </label>
            <input
              id="repo-search"
              type="search"
              placeholder="Search by name or slug…"
              className="input max-w-xs"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setOffset(0)
              }}
            />
          </Filters>

          {isLoading && !data ? (
            <Loading />
          ) : !data || data.items.length === 0 ? (
            <EmptyState
              icon="⌥"
              title={search ? 'No repositories match that search' : 'No repositories yet'}
              description={
                search
                  ? 'Try a different term.'
                  : 'Register a public HTTPS repository to start building it.'
              }
            />
          ) : (
            <>
              <Table
                caption="Registered repositories"
                columns={['Repository', 'Branch', 'Last pipeline', 'Registered', '']}
              >
                {data.items.map((repository) => (
                  <tr key={repository.id} className="row-link">
                    <td className="td">
                      <Link
                        to={`/repositories/${repository.id}`}
                        className="font-medium text-ink hover:text-accent"
                      >
                        {repository.name}
                      </Link>
                      <div className="mono mt-0.5 text-ink-faint">{repository.slug}</div>
                    </td>
                    <td className="td mono">{repository.default_branch}</td>
                    <td className="td">{formatRelative(repository.last_pipeline_at)}</td>
                    <td className="td">{formatRelative(repository.created_at)}</td>
                    <td className="td text-right">
                      {can('repo:delete') && (
                        <button
                          type="button"
                          className="btn-danger px-2 py-1 text-2xs"
                          onClick={() => {
                            if (
                              window.confirm(
                                `Remove ${repository.name}? Pipeline history and audit ` +
                                  `records are retained.`,
                              )
                            ) {
                              remove.mutate(repository.id)
                            }
                          }}
                        >
                          Remove
                        </button>
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
        </div>
      )}
    </div>
  )
}
