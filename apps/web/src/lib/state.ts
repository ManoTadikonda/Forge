/** Semantic mapping from domain states to presentation. */

import type { DeploymentState, PipelineState, StageStatus } from './types'

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral'

export interface StateMeta {
  tone: Tone
  label: string
  /** True while work is in flight, which drives polling and the pulse indicator. */
  active: boolean
}

const PIPELINE: Record<PipelineState, StateMeta> = {
  QUEUED: { tone: 'neutral', label: 'Queued', active: true },
  CLONING: { tone: 'info', label: 'Cloning', active: true },
  VALIDATING: { tone: 'info', label: 'Validating', active: true },
  TESTING: { tone: 'info', label: 'Testing', active: true },
  BUILDING: { tone: 'info', label: 'Building', active: true },
  PUBLISHING: { tone: 'info', label: 'Publishing', active: true },
  SUCCEEDED: { tone: 'ok', label: 'Succeeded', active: false },
  FAILED: { tone: 'danger', label: 'Failed', active: false },
  CANCELED: { tone: 'neutral', label: 'Canceled', active: false },
}

const DEPLOYMENT: Record<DeploymentState, StateMeta> = {
  PENDING: { tone: 'neutral', label: 'Pending', active: true },
  PREPARING: { tone: 'info', label: 'Preparing', active: true },
  DEPLOYING: { tone: 'info', label: 'Deploying', active: true },
  VERIFYING: { tone: 'info', label: 'Verifying', active: true },
  CANARY: { tone: 'warn', label: 'Canary', active: true },
  PROMOTING: { tone: 'info', label: 'Promoting', active: true },
  SUCCEEDED: { tone: 'ok', label: 'Succeeded', active: false },
  FAILED: { tone: 'danger', label: 'Failed', active: false },
  CANCELED: { tone: 'neutral', label: 'Canceled', active: false },
  ROLLING_BACK: { tone: 'danger', label: 'Rolling back', active: true },
  ROLLED_BACK: { tone: 'warn', label: 'Rolled back', active: false },
  // Distinct from ROLLED_BACK: recovery could not be confirmed, so this needs
  // a human. It must not look like a tidy outcome.
  ROLLBACK_FAILED: { tone: 'danger', label: 'Rollback failed', active: false },
}

const STAGE: Record<StageStatus, StateMeta> = {
  PENDING: { tone: 'neutral', label: 'Pending', active: false },
  RUNNING: { tone: 'info', label: 'Running', active: true },
  SUCCEEDED: { tone: 'ok', label: 'Succeeded', active: false },
  FAILED: { tone: 'danger', label: 'Failed', active: false },
  SKIPPED: { tone: 'neutral', label: 'Skipped', active: false },
}

const FALLBACK: StateMeta = { tone: 'neutral', label: 'Unknown', active: false }

export function pipelineMeta(state: string): StateMeta {
  return PIPELINE[state as PipelineState] ?? FALLBACK
}

export function deploymentMeta(state: string): StateMeta {
  return DEPLOYMENT[state as DeploymentState] ?? FALLBACK
}

export function stageMeta(status: string): StateMeta {
  return STAGE[status as StageStatus] ?? FALLBACK
}

export function verdictTone(verdict: string): Tone {
  if (verdict === 'HEALTHY') return 'ok'
  if (verdict === 'UNHEALTHY') return 'danger'
  // UNKNOWN is deliberately a warning, never a success: it means Forge could not
  // confirm health, which is not the same as confirming good health.
  return 'warn'
}

export const TONE_CLASSES: Record<Tone, string> = {
  ok: 'bg-ok-soft text-ok border-ok/30',
  warn: 'bg-warn-soft text-warn border-warn/30',
  danger: 'bg-danger-soft text-danger border-danger/30',
  info: 'bg-info-soft text-info border-info/30',
  neutral: 'bg-surface-overlay text-ink-muted border-surface-border',
}

export const TONE_DOT: Record<Tone, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-ink-faint',
}
