/**
 * TanStack Query hooks.
 *
 * Polling policy: a resource that is doing work polls; a finished one does not.
 * `refetchInterval` returns false once the entity reaches a terminal state, so an
 * idle dashboard makes no requests at all.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import { api, ApiError, type ListParams } from '@/lib/api'
import { deploymentMeta, pipelineMeta } from '@/lib/state'
import type {
  Artifact, AuditEvent, CurrentUser, Deployment, DeploymentDetail,
  Environment, Incident, LogLine, Overview, Page, Pipeline, PipelineDetail,
  PlatformSettings, Repository, RepositoryDetail, Service, ServiceHealth,
} from '@/lib/types'

const ACTIVE_POLL_MS = 2000
const LIST_POLL_MS = 5000

export const keys = {
  me: ['me'] as const,
  overview: ['overview'] as const,
  settings: ['settings'] as const,
  incidents: ['incidents'] as const,
  repositories: (p: ListParams) => ['repositories', p] as const,
  repository: (id: string) => ['repository', id] as const,
  pipelines: (p: ListParams) => ['pipelines', p] as const,
  pipeline: (id: string) => ['pipeline', id] as const,
  pipelineLogs: (id: string) => ['pipeline-logs', id] as const,
  deployments: (p: ListParams) => ['deployments', p] as const,
  deployment: (id: string) => ['deployment', id] as const,
  environments: ['environments'] as const,
  services: (environment?: string) => ['services', environment ?? ''] as const,
  serviceHealth: (id: string) => ['service-health', id] as const,
  artifacts: (p: ListParams) => ['artifacts', p] as const,
  audit: (p: ListParams) => ['audit', p] as const,
}

export function useCurrentUser(): UseQueryResult<CurrentUser> {
  return useQuery({
    queryKey: keys.me,
    queryFn: api.me,
    retry: (count, error) =>
      // An unauthenticated response is an answer, not a transient failure.
      !(error instanceof ApiError && error.isAuth) && count < 2,
    staleTime: 60_000,
  })
}

export function useOverview(): UseQueryResult<Overview> {
  return useQuery({
    queryKey: keys.overview,
    queryFn: api.overview,
    refetchInterval: LIST_POLL_MS,
  })
}

export function useRepositories(params: ListParams = {}) {
  return useQuery({
    queryKey: keys.repositories(params),
    queryFn: () => api.repositories(params),
    placeholderData: (previous: Page<Repository> | undefined) => previous,
  })
}

export function useRepository(id: string): UseQueryResult<RepositoryDetail> {
  return useQuery({
    queryKey: keys.repository(id),
    queryFn: () => api.repository(id),
    enabled: Boolean(id),
  })
}

export function usePipelines(params: ListParams = {}) {
  return useQuery({
    queryKey: keys.pipelines(params),
    queryFn: () => api.pipelines(params),
    placeholderData: (previous: Page<Pipeline> | undefined) => previous,
    refetchInterval: (q) =>
      (q.state.data?.items ?? []).some((p) => pipelineMeta(p.state).active)
        ? ACTIVE_POLL_MS
        : false,
  })
}

export function usePipeline(id: string): UseQueryResult<PipelineDetail> {
  return useQuery({
    queryKey: keys.pipeline(id),
    queryFn: () => api.pipeline(id),
    enabled: Boolean(id),
    refetchInterval: (q) =>
      q.state.data && pipelineMeta(q.state.data.state).active ? ACTIVE_POLL_MS : false,
  })
}

export function usePipelineLogs(id: string, active: boolean) {
  return useQuery<LogLine[]>({
    queryKey: keys.pipelineLogs(id),
    queryFn: () => api.pipelineLogs(id, { limit: 500 }),
    enabled: Boolean(id),
    refetchInterval: active ? ACTIVE_POLL_MS : false,
  })
}

export function useDeployments(params: ListParams = {}) {
  return useQuery({
    queryKey: keys.deployments(params),
    queryFn: () => api.deployments(params),
    placeholderData: (previous: Page<Deployment> | undefined) => previous,
    refetchInterval: (q) =>
      (q.state.data?.items ?? []).some((d) => deploymentMeta(d.state).active)
        ? ACTIVE_POLL_MS
        : false,
  })
}

export function useDeployment(id: string): UseQueryResult<DeploymentDetail> {
  return useQuery({
    queryKey: keys.deployment(id),
    queryFn: () => api.deployment(id),
    enabled: Boolean(id),
    // A rollout changes quickly; poll faster while it is in flight.
    refetchInterval: (q) =>
      q.state.data && deploymentMeta(q.state.data.state).active ? 1500 : false,
  })
}

export function useEnvironments(): UseQueryResult<Environment[]> {
  return useQuery({ queryKey: keys.environments, queryFn: api.environments })
}

export function useServices(environment?: string): UseQueryResult<Service[]> {
  return useQuery({
    queryKey: keys.services(environment),
    queryFn: () => api.services(environment),
    refetchInterval: LIST_POLL_MS,
  })
}

export function useServiceHealth(id: string): UseQueryResult<ServiceHealth> {
  return useQuery({
    queryKey: keys.serviceHealth(id),
    queryFn: () => api.serviceHealth(id),
    enabled: Boolean(id),
    refetchInterval: 3000,
  })
}

export function useArtifacts(params: ListParams = {}) {
  return useQuery({
    queryKey: keys.artifacts(params),
    queryFn: () => api.artifacts(params),
    placeholderData: (previous: Page<Artifact> | undefined) => previous,
  })
}

export function useAudit(params: ListParams = {}) {
  return useQuery({
    queryKey: keys.audit(params),
    queryFn: () => api.audit(params),
    placeholderData: (previous: Page<AuditEvent> | undefined) => previous,
  })
}

export function useIncidents(): UseQueryResult<Incident[]> {
  return useQuery({
    queryKey: keys.incidents,
    queryFn: api.incidents,
    refetchInterval: LIST_POLL_MS,
  })
}

export function useSettings(): UseQueryResult<PlatformSettings> {
  return useQuery({ queryKey: keys.settings, queryFn: api.settings })
}

// ------------------------------------------------------------------- mutations

export function useTriggerPipeline() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.triggerPipeline,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['pipelines'] })
      void client.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useCancelPipeline() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.cancelPipeline,
    onSuccess: (_data, id) => {
      void client.invalidateQueries({ queryKey: keys.pipeline(id) })
      void client.invalidateQueries({ queryKey: ['pipelines'] })
    },
  })
}

export function useCreateRepository() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.createRepository,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['repositories'] })
      void client.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useDeleteRepository() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteRepository,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['repositories'] })
    },
  })
}

export function useCreateDeployment() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.createDeployment,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['deployments'] })
      void client.invalidateQueries({ queryKey: keys.overview })
    },
  })
}

export function useCancelDeployment() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.cancelDeployment,
    onSuccess: (_data, id) => {
      void client.invalidateQueries({ queryKey: keys.deployment(id) })
      void client.invalidateQueries({ queryKey: ['deployments'] })
    },
  })
}

export function useRollbackDeployment() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.rollbackDeployment,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['deployments'] })
      void client.invalidateQueries({ queryKey: keys.overview })
    },
  })
}
