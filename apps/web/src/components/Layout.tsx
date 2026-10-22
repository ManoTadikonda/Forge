/** Application shell: sidebar navigation, header and content region. */

import { NavLink, Outlet } from 'react-router-dom'
import { useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useOverview } from '@/hooks/queries'

interface NavItem {
  to: string
  label: string
  icon: string
  /** Hidden when the user lacks this permission. Cosmetic only. */
  permission?: string
}

const NAV: { heading: string; items: NavItem[] }[] = [
  {
    heading: 'Delivery',
    items: [
      { to: '/', label: 'Overview', icon: '◳' },
      { to: '/repositories', label: 'Repositories', icon: '⌥' },
      { to: '/pipelines', label: 'Pipelines', icon: '⚙' },
      { to: '/deployments', label: 'Deployments', icon: '⇪' },
    ],
  },
  {
    heading: 'Operations',
    items: [
      { to: '/environments', label: 'Environments', icon: '◰' },
      { to: '/metrics', label: 'Metrics', icon: '◭' },
    ],
  },
  {
    heading: 'Governance',
    items: [
      { to: '/security', label: 'Security & SBOM', icon: '⛉' },
      { to: '/audit', label: 'Audit log', icon: '☰', permission: 'audit:read' },
      { to: '/settings', label: 'Settings', icon: '⚒' },
    ],
  },
]

function Badge({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <span className="ml-auto rounded-full bg-accent-soft px-1.5 py-0.5
                     text-2xs font-medium text-accent tabular">
      {count}
    </span>
  )
}

export function Layout() {
  const { user, logout, can } = useAuth()
  const { data: overview } = useOverview()
  const [menuOpen, setMenuOpen] = useState(false)

  const activeCounts: Record<string, number> = {
    '/pipelines': overview?.stats.pipelines_running ?? 0,
    '/deployments': overview?.stats.deployments_active ?? 0,
    '/security': overview?.stats.open_incidents ?? 0,
  }

  function handleLogout(): void {
    // logout() performs a full navigation to /login itself.
    void logout()
  }

  return (
    <div className="flex h-full">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4
                   focus:z-50 focus:rounded focus:bg-accent focus:px-3 focus:py-2
                   focus:text-sm focus:text-white"
      >
        Skip to content
      </a>

      <aside
        className={`fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r
                    border-surface-border bg-surface-raised transition-transform
                    lg:static lg:translate-x-0
                    ${menuOpen ? 'translate-x-0' : '-translate-x-full'}`}
      >
        <div className="flex h-14 items-center gap-2 border-b border-surface-border px-4">
          <div className="flex h-7 w-7 items-center justify-center rounded
                          bg-accent text-sm font-bold text-white">
            F
          </div>
          <div>
            <div className="text-sm font-semibold leading-none">Forge</div>
            <div className="text-2xs text-ink-faint">Delivery platform</div>
          </div>
        </div>

        <nav aria-label="Main" className="flex flex-col gap-5 overflow-y-auto p-3">
          {NAV.map((group) => {
            const items = group.items.filter(
              (item) => !item.permission || can(item.permission),
            )
            if (items.length === 0) return null
            return (
              <div key={group.heading}>
                <div className="label px-2 pb-1.5">{group.heading}</div>
                <ul className="space-y-0.5">
                  {items.map((item) => (
                    <li key={item.to}>
                      <NavLink
                        to={item.to}
                        end={item.to === '/'}
                        onClick={() => setMenuOpen(false)}
                        className={({ isActive }) =>
                          `flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm
                           transition-colors ${
                             isActive
                               ? 'bg-accent-soft font-medium text-accent'
                               : 'text-ink-muted hover:bg-surface-overlay hover:text-ink'
                           }`
                        }
                      >
                        <span aria-hidden="true" className="w-4 text-center opacity-70">
                          {item.icon}
                        </span>
                        {item.label}
                        <Badge count={activeCounts[item.to] ?? 0} />
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
        </nav>
      </aside>

      {menuOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setMenuOpen(false)}
        />
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b
                           border-surface-border bg-surface-raised px-4">
          <button
            type="button"
            className="btn-ghost px-2 py-1 lg:hidden"
            aria-label="Open navigation"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            ☰
          </button>
          <div className="ml-auto flex items-center gap-3">
            {user && (
              <div className="hidden text-right sm:block">
                <div className="text-sm leading-none text-ink">{user.email}</div>
                <div className="mt-0.5 text-2xs text-ink-faint">{user.role}</div>
              </div>
            )}
            <button type="button" onClick={handleLogout} className="btn-ghost">
              Sign out
            </button>
          </div>
        </header>

        <main id="main" className="min-w-0 flex-1 overflow-y-auto p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
