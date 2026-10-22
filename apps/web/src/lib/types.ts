/**
 * Response shapes mirroring the API's pydantic schemas.
 *
 * These are hand-maintained rather than generated: the surface is small, and an
 * explicit type is the place where a drift between client and server shows up as a
 * compile error. `npm run typecheck` is what catches a renamed field.
 */

export type Role = 'VIEWER' | 'DEVELOPER' | 'ADMIN'

export type PipelineState =
  | 'QUEUED' | 'CLONING' | 'VALIDATING' | 'TESTING' | 'BUILDING'
  | 'PUBLISHING' | 'SUCCEEDED' | 'FAILED' | 'CANCELED'

export type DeploymentState =
  | 'PENDING' | 'PREPARING' | 'DEPLOYING' | 'VERIFYING' | 'CANARY'
  | 'PROMOTING' | 'SUCCEEDED' | 'FAILED' | 'CANCELED'
  | 'ROLLING_BACK' | 'ROLLED_BACK' | 'ROLLBACK_FAILED'

export type StageStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'SKIPPED'

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface CurrentUser {
  id: string
  email: string
  display_name: string
  role: Role
  is_active: boolean
  last_login_at: string | null
  created_at: string
  permissions: string[]
}

export interface Repository {
  id: string
  name: string
  url: string
  slug: string
  default_branch: string
  description: string
  is_active: boolean
  webhook_enabled: boolean
  last_pipeline_at: string | null
  created_at: string
}

export interface RepositoryDetail extends Repository {
  pipeline_count: number
  recent_pipelines: Pipeline[]
}

export interface Stage {
  id: string
  name: string
  sequence: number
  status: StageStatus
  exit_code: number | null
  message: string
  skip_reason: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface Pipeline {
  id: string
  repository_id: string | null
  repository_label: string
  run_number: number
  state: PipelineState
  branch: string
  commit_sha: string
  commit_message: string
  commit_author: string
  trigger: string
  failure_reason: string
  cancel_requested: boolean
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  created_at: string
}

export interface PipelineDetail extends Pipeline {
  stages: Stage[]
  artifacts: Artifact[]
  manifest: Record<string, unknown> | null
}

export interface LogLine {
  sequence: number
  stage: string
  content: string
  truncated: boolean
  created_at: string
}

export interface Artifact {
  id: string
  digest: string
  image_name: string
  version: string
  registry_name: string
  reference: string
  size_bytes: number
  pipeline_run_id: string | null
  repository_url: string
  commit_sha: string
  builder: string
  provenance: Record<string, string>
  sbom_format: string
  sbom_component_count: number
  signed: boolean
  signature_reference: string
  scan_status: string
  scan_skip_reason: string
  created_at: string
}

export interface DeploymentEvent {
  id: string
  sequence: number
  kind: string
  from_state: string
  to_state: string
  message: string
  actor: string
  payload: Record<string, unknown>
  created_at: string
}

export interface HealthSample {
  id: string
  release_role: string
  version: string
  observed_at: string
  requests: number
  errors: number
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  latency_p99_ms: number | null
  healthy_endpoint: boolean
  ready_endpoint: boolean
}

export interface Deployment {
  id: string
  service_id: string
  environment_id: string
  artifact_id: string
  previous_artifact_id: string | null
  state: DeploymentState
  deployment_number: number
  strategy: string
  current_step_index: number
  current_weight: number
  failure_reason: string
  rollback_reason: string
  rollback_verified: boolean
  health_summary: HealthSummary
  cancel_requested: boolean
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  created_at: string
}

export interface HealthSummary {
  weight?: number
  candidate?: HealthAssessment
  stable?: HealthAssessment
  evaluated_at?: string
}

export interface HealthAssessment {
  verdict: 'HEALTHY' | 'UNHEALTHY' | 'UNKNOWN'
  reason: string
  requests: number
  errors: number
  error_rate: number
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  latency_p99_ms: number | null
  breaches: string[]
}

export interface DeploymentDetail extends Deployment {
  service_name: string
  environment_name: string
  version: string
  artifact_digest: string
  previous_version: string
  events: DeploymentEvent[]
  health_samples: HealthSample[]
  canary_plan: number[]
}

export interface Environment {
  id: string
  name: string
  display_name: string
  is_protected: boolean
  created_at: string
}

export interface Service {
  id: string
  name: string
  environment_id: string
  environment_name: string
  repository_id: string | null
  current_artifact_id: string | null
  current_version: string
  created_at: string
}

export interface Release {
  id: string
  version: string
  role: string
  weight: number
  is_running: boolean
  endpoints: Record<string, unknown>
  created_at: string
  stopped_at: string | null
}

export interface ServiceHealth {
  service: Service
  releases: Release[]
  router_weights: Record<string, number>
  router_reachable: boolean
  last_deployment_state: string
}

export interface Incident {
  id: string
  deployment_id: string | null
  service_id: string | null
  kind: string
  severity: string
  title: string
  detail: string
  verdict: string
  evidence: Record<string, unknown>
  resolved_at: string | null
  created_at: string
}

export interface AuditEvent {
  id: string
  actor_label: string
  action: string
  resource_type: string
  resource_id: string
  outcome: 'SUCCESS' | 'FAILURE' | 'DENIED'
  ip_address: string
  request_id: string
  detail: Record<string, unknown>
  created_at: string
}

export interface OverviewStats {
  repositories: number
  pipelines_total: number
  pipelines_succeeded: number
  pipelines_failed: number
  pipelines_running: number
  /** null (not 0) when nothing has finished — "no data" is not "0% success". */
  pipeline_success_rate: number | null
  deployments_total: number
  deployments_succeeded: number
  deployments_rolled_back: number
  deployments_active: number
  artifacts: number
  open_incidents: number
  queue_depth: Record<string, number>
}

export interface Overview {
  stats: OverviewStats
  recent_pipelines: Pipeline[]
  recent_deployments: Deployment[]
  recent_incidents: Incident[]
}

export interface PlatformSettings {
  environment: string
  version: string
  builder: string
  registry: string
  deploy_adapter: string
  canary_steps: number[]
  canary_step_seconds: number
  health_min_samples: number
  health_max_error_rate: number
  health_max_p95_latency_ms: number
  rate_limit_enabled: boolean
  webhook_secret_configured: boolean
  redis_configured: boolean
  rate_limiter_backend: string
  router_admin_url: string
  unsandboxed_builds_allowed: boolean
}
