import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div className="flex min-h-full flex-col items-center justify-center p-6 text-center">
      <p className="mono text-ink-faint">404</p>
      <h1 className="mt-2 text-lg font-semibold text-ink">Page not found</h1>
      <p className="mt-1 text-sm text-ink-muted">
        That route does not exist in Forge.
      </p>
      <Link to="/" className="btn-primary mt-5">
        Back to overview
      </Link>
    </div>
  )
}
