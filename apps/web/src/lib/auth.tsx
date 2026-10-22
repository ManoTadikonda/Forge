/** Authentication context.
 *
 * The session itself lives in an HttpOnly cookie that JavaScript cannot read; this
 * context only mirrors who the server says we are. `permissions` is used to hide
 * controls the user cannot use — a usability affordance, not a security boundary.
 * Every operation is authorised server-side regardless of what the UI shows.
 */

import {
  createContext, useCallback, useContext, useMemo, type ReactNode,
} from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { useCurrentUser } from '@/hooks/queries'
import type { CurrentUser } from './types'

interface AuthValue {
  user: CurrentUser | null
  isLoading: boolean
  isAuthenticated: boolean
  can: (permission: string) => boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const { data, isLoading, isFetched } = useCurrentUser()

  const login = useCallback(
    async (email: string, password: string) => {
      await api.login(email, password)
      await client.invalidateQueries()
    },
    [client],
  )

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      // Clear cached data either way: a failed logout must not leave another
      // user's data visible in the cache.
      client.clear()
      await client.invalidateQueries()
    }
  }, [client])

  const value = useMemo<AuthValue>(() => {
    const permissions = new Set(data?.permissions ?? [])
    return {
      user: data ?? null,
      isLoading: isLoading && !isFetched,
      isAuthenticated: Boolean(data),
      can: (permission: string) => permissions.has(permission),
      login,
      logout,
    }
  }, [data, isLoading, isFetched, login, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/* eslint-disable react-refresh/only-export-components --
   The provider and its hook belong together: splitting them would export the
   context object itself, widening the surface for no benefit. */
export function useAuth(): AuthValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside an AuthProvider')
  return context
}
