/** Typed API client. */

import type {
  Artifact, AuditEvent, CurrentUser, Deployment, DeploymentDetail,
  DeploymentEvent, Environment, Incident, LogLine, Overview, Page,
  Pipeline, PipelineDetail, PlatformSettings, Repository, RepositoryDetail,
  Service, ServiceHealth,
} from './types'

const BASE = '/api/v1'

/** An API error carrying the server's stable code and correlation id. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId = '',
    readonly details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  get isAuth(): boolean {
    return this.status === 401
  }

  get isForbidden(): boolean {
    return this.status === 403
  }
}

interface ErrorBody {
  error?: { code?: string; message?: string; details?: unknown }
  request_id?: string
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)forge_csrf=([^;]*)/)
  return match?.[1] ? decodeURIComponent(match[1]) : ''
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  // Cookie-authenticated writes carry the double-submit CSRF token.
  if (method !== 'GET' && method !== 'HEAD') {
    const token = csrfToken()
    if (token) headers.set('X-CSRF-Token', token)
  }

  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload: unknown = text ? JSON.parse(text) : null

  if (!response.ok) {
    const body = (payload ?? {}) as ErrorBody
    throw new ApiError(
      response.status,
      body.error?.code ?? 'unknown_error',
      body.error?.message ?? `Request failed with status ${response.status}`,
      body.request_id ?? '',
      body.error?.details,
    )
  }
  return payload as T
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const serialised = search.toString()
  return serialised ? `?${serialised}` : ''
}

export interface ListParams {
  limit?: number
  offset?: number
  [key: string]: string | number | undefined
}

export const api = {
  // --- auth ---
  login: (email: string, password: string) =>
    request<{ access_token: string; expires_at: string; csrf_token: string }>(
      '/auth/login',
      { method: 'POST', body: JSON.stringify({ email, password }) },
    ),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  me: () => request<CurrentUser>('/auth/me'),

  // --- overview ---
  overview: () => request<Overview>('/overview'),
  incidents: () => request<Incident[]>('/incidents'),

  // --- repositories ---
  repositories: (p: ListParams = {}) =>
    request<Page<Repository>>(`/repositories${query(p)}`),
  repository: (id: string) => request<RepositoryDetail>(`/repositories/${id}`),
  createRepository: (body: {
    url: string
    name?: string
    default_branch?: string
    description?: string
  }) => request<Repository>('/repositories', {
    method: 'POST', body: JSON.stringify(body),
  }),
  deleteRepository: (id: string) =>
    request<void>(`/repositories/${id}`, { method: 'DELETE' }),

  // --- pipelines ---
  pipelines: (p: ListParams = {}) => request<Page<Pipeline>>(`/pipelines${query(p)}`),
  pipeline: (id: string) => request<PipelineDetail>(`/pipelines/${id}`),
  pipelineLogs: (id: string, p: ListParams = {}) =>
    request<LogLine[]>(`/pipelines/${id}/logs${query(p)}`),
  triggerPipeline: (body: { repository_id: string; branch?: string }) =>
    request<Pipeline>('/pipelines', { method: 'POST', body: JSON.stringify(body) }),
  cancelPipeline: (id: string) =>
    request<Pipeline>(`/pipelines/${id}/cancel`, { method: 'POST' }),

  // --- deployments ---
  deployments: (p: ListParams = {}) =>
    request<Page<Deployment>>(`/deployments${query(p)}`),
  deployment: (id: string) => request<DeploymentDetail>(`/deployments/${id}`),
  deploymentEvents: (id: string, after = -1) =>
    request<DeploymentEvent[]>(`/deployments/${id}/events${query({ after })}`),
  createDeployment: (body: {
    artifact_digest?: string
    artifact_id?: string
    service_name?: string
    environment?: string
    canary_steps?: number[]
    step_seconds?: number
    release_env?: Record<string, string>
  }) => request<Deployment>('/deployments', {
    method: 'POST', body: JSON.stringify(body),
  }),
  cancelDeployment: (id: string) =>
    request<Deployment>(`/deployments/${id}/cancel`, { method: 'POST' }),
  rollbackDeployment: (id: string) =>
    request<Deployment>(`/deployments/${id}/rollback`, { method: 'POST' }),

  // --- platform ---
  environments: () => request<Environment[]>('/environments'),
  services: (environment?: string) =>
    request<Service[]>(`/services${query({ environment })}`),
  serviceHealth: (id: string) => request<ServiceHealth>(`/services/${id}/health`),

  // --- supply chain ---
  artifacts: (p: ListParams = {}) => request<Page<Artifact>>(`/artifacts${query(p)}`),
  artifact: (id: string) => request<Artifact>(`/artifacts/${id}`),
  sbomUrl: (id: string) => `${BASE}/artifacts/${id}/sbom`,

  // --- governance ---
  audit: (p: ListParams = {}) => request<Page<AuditEvent>>(`/audit${query(p)}`),
  settings: () => request<PlatformSettings>('/admin/settings'),
}
