import { describe, expect, it } from 'vitest'
import {
  formatBytes, formatDuration, formatPercent, shortDigest, shortSha, titleCase,
} from './format'

describe('formatDuration', () => {
  it('renders sub-second values in milliseconds', () => {
    expect(formatDuration(450)).toBe('450ms')
  })

  it('renders seconds, minutes and hours', () => {
    expect(formatDuration(1500)).toBe('1.5s')
    expect(formatDuration(90_000)).toBe('1m 30s')
    expect(formatDuration(3_900_000)).toBe('1h 5m')
  })

  it('distinguishes missing values from zero', () => {
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(undefined)).toBe('—')
    expect(formatDuration(0)).toBe('0ms')
  })
})

describe('formatPercent', () => {
  it('renders a fraction as a percentage', () => {
    expect(formatPercent(0.4545, 2)).toBe('45.45%')
    expect(formatPercent(1)).toBe('100.0%')
  })

  it('shows a dash for null rather than 0%', () => {
    // "No data" and "zero percent" are different claims; conflating them would
    // let an empty platform appear to have a 0% success rate.
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(0)).toBe('0.0%')
  })
})

describe('formatBytes', () => {
  it('scales units', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(30_720)).toBe('30.0 KB')
    expect(formatBytes(5_242_880)).toBe('5.0 MB')
  })
})

describe('digest and sha helpers', () => {
  it('strips the algorithm prefix', () => {
    expect(shortDigest('sha256:abcdef0123456789')).toBe('abcdef012345')
  })

  it('shortens a commit sha', () => {
    expect(shortSha('8d3febd62c17aaaa')).toBe('8d3febd')
    expect(shortSha('')).toBe('—')
  })
})

describe('titleCase', () => {
  it('humanises snake_case event kinds', () => {
    expect(titleCase('rollback_started')).toBe('Rollback Started')
    expect(titleCase('error_rate')).toBe('Error Rate')
  })
})
