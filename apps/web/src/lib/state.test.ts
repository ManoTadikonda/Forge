import { describe, expect, it } from 'vitest'
import { deploymentMeta, pipelineMeta, stageMeta, verdictTone } from './state'

describe('pipelineMeta', () => {
  it('marks in-flight states active so the UI polls', () => {
    for (const state of ['QUEUED', 'CLONING', 'TESTING', 'BUILDING'] as const) {
      expect(pipelineMeta(state).active).toBe(true)
    }
  })

  it('marks terminal states inactive so polling stops', () => {
    for (const state of ['SUCCEEDED', 'FAILED', 'CANCELED'] as const) {
      expect(pipelineMeta(state).active).toBe(false)
    }
  })

  it('tones failure as danger and success as ok', () => {
    expect(pipelineMeta('FAILED').tone).toBe('danger')
    expect(pipelineMeta('SUCCEEDED').tone).toBe('ok')
  })

  it('falls back safely for an unrecognised state', () => {
    expect(pipelineMeta('NONSENSE').label).toBe('Unknown')
  })
})

describe('deploymentMeta', () => {
  it('separates a verified rollback from a failed one', () => {
    // ROLLBACK_FAILED needs a human; it must not look like a tidy outcome.
    expect(deploymentMeta('ROLLED_BACK').tone).toBe('warn')
    expect(deploymentMeta('ROLLBACK_FAILED').tone).toBe('danger')
  })

  it('treats rolling back as active work', () => {
    expect(deploymentMeta('ROLLING_BACK').active).toBe(true)
  })
})

describe('verdictTone', () => {
  it('never presents UNKNOWN as success', () => {
    // UNKNOWN means health could not be confirmed, which is not good health.
    expect(verdictTone('UNKNOWN')).toBe('warn')
    expect(verdictTone('HEALTHY')).toBe('ok')
    expect(verdictTone('UNHEALTHY')).toBe('danger')
  })
})

describe('stageMeta', () => {
  it('marks a running stage active and a skipped one neutral', () => {
    expect(stageMeta('RUNNING').active).toBe(true)
    expect(stageMeta('SKIPPED').tone).toBe('neutral')
  })
})
