import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '@/lib/auth'
import { Loading, Spinner } from '@/components/primitives'

export function LoginPage() {
  const { login, isAuthenticated, isLoading } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  if (isLoading) return <Loading label="Checking your session…" />
  if (isAuthenticated) return <Navigate to="/" replace />

  function handleSubmit(event: FormEvent): void {
    event.preventDefault()
    void submit()
  }

  async function submit(): Promise<void> {
    setError('')
    setPending(true)
    try {
      await login(email, password)
      void navigate('/', { replace: true })
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : 'Sign in failed. Try again.',
      )
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-7 text-center">
          <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center
                          rounded-lg bg-accent text-lg font-bold text-white">
            F
          </div>
          <h1 className="text-lg font-semibold text-ink">Sign in to Forge</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Build, deploy and roll back your services.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="card card-pad space-y-4">
          <div>
            <label htmlFor="email" className="label mb-1.5 block">
              Email address
            </label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="username"
              required
              className="input"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div>
            <label htmlFor="password" className="label mb-1.5 block">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              className="input"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {error && (
            <p role="alert" className="rounded border border-danger/30 bg-danger-soft
                                       px-3 py-2 text-sm text-danger">
              {error}
            </p>
          )}

          <button type="submit" disabled={pending} className="btn-primary w-full">
            {pending ? <Spinner label="Signing in" /> : 'Sign in'}
          </button>
        </form>

        <p className="mt-4 text-center text-2xs text-ink-faint">
          No account exists by default. An administrator creates the first one.
        </p>
      </div>
    </div>
  )
}
