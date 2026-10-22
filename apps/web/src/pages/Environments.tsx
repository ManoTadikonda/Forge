import { useState } from 'react'
import { useEnvironments, useServiceHealth, useServices } from '@/hooks/queries'
import {
  EmptyState, ErrorState, Loading, PageHeader, Tone,
} from '@/components/primitives'
import { deploymentMeta } from '@/lib/state'
import { formatRelative } from '@/lib/format'

function ServiceCard({ serviceId }: { serviceId: string }) {
  const { data, isLoading } = useServiceHealth(serviceId)
  if (isLoading || !data) {
    return (
      <div className="card card-pad">
        <Loading />
      </div>
    )
  }

  const { service, releases, router_weights: weights, router_reachable: reachable } = data
  const running = releases.filter((release) => release.is_running)

  return (
    <article className="card">
      <div className="flex items-center justify-between border-b border-surface-border
                       px-4 py-3">
        <div>
          <h3 className="text-sm font-medium text-ink">{service.name}</h3>
          <p className="mt-0.5 text-2xs text-ink-faint">
            {service.environment_name} · v{service.current_version || 'none'}
          </p>
        </div>
        {data.last_deployment_state && (
          <Tone tone={deploymentMeta(data.last_deployment_state).tone}>
            {deploymentMeta(data.last_deployment_state).label}
          </Tone>
        )}
      </div>

      <div className="card-pad space-y-3">
        <div>
          <div className="label mb-1.5">Live traffic split</div>
          {!reachable ? (
            /* Reporting our intent as if it were reality would hide a drift bug. */
            <p className="text-sm text-warn">
              Router is unreachable — live weights cannot be confirmed.
            </p>
          ) : Object.keys(weights).length === 0 ? (
            <p className="text-sm text-ink-muted">No route registered.</p>
          ) : (
            <ul className="space-y-1.5">
              {Object.entries(weights).map(([release, weight]) => (
                <li key={release}>
                  <div className="flex items-center justify-between text-2xs">
                    <span className="truncate text-ink-muted">{release}</span>
                    <span className="tabular ml-2 text-ink">{weight}%</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full
                                  bg-surface-overlay">
                    <div
                      className={weight > 0 ? 'h-full bg-accent' : 'h-full'}
                      style={{ width: `${weight}%` }}
                      role="img"
                      aria-label={`${release} receives ${weight} percent of traffic`}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <div className="label mb-1.5">Running releases ({running.length})</div>
          {running.length === 0 ? (
            <p className="text-sm text-ink-muted">Nothing is running.</p>
          ) : (
            <ul className="space-y-1">
              {running.map((release) => (
                <li
                  key={release.id}
                  className="flex items-center justify-between text-2xs"
                >
                  <span className="text-ink">v{release.version}</span>
                  <span className="text-ink-faint">
                    {release.role} · {formatRelative(release.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </article>
  )
}

export function EnvironmentsPage() {
  const [environment, setEnvironment] = useState('')
  const { data: environments } = useEnvironments()
  const { data: services, isLoading, error, refetch } = useServices(environment)

  return (
    <div>
      <PageHeader
        title="Environments & services"
        description="Live routing weights and running releases, read from the router itself."
        actions={
          <select
            aria-label="Filter by environment"
            className="input max-w-[12rem]"
            value={environment}
            onChange={(event) => setEnvironment(event.target.value)}
          >
            <option value="">All environments</option>
            {(environments ?? []).map((item) => (
              <option key={item.id} value={item.name}>
                {item.display_name || item.name}
              </option>
            ))}
          </select>
        }
      />

      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading && !services ? (
        <Loading />
      ) : !services || services.length === 0 ? (
        <div className="card">
          <EmptyState
            icon="◰"
            title="No services deployed"
            description="A service appears here after its first deployment."
          />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {services.map((service) => (
            <ServiceCard key={service.id} serviceId={service.id} />
          ))}
        </div>
      )}
    </div>
  )
}
