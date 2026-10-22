/** Time-series charts for a deployment's health samples. */

import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { HealthSample } from '@/lib/types'
import { EmptyState } from './primitives'

const CANDIDATE = '#f5a623'
const STABLE = '#3ecf8e'

interface Point {
  time: string
  candidateErrorRate?: number
  stableErrorRate?: number
  candidateP95?: number
  stableP95?: number
}

function toPoints(samples: HealthSample[]): Point[] {
  const byTime = new Map<string, Point>()
  for (const sample of samples) {
    const time = new Date(sample.observed_at).toLocaleTimeString()
    const point = byTime.get(time) ?? { time }
    const rate = sample.requests > 0 ? (sample.errors / sample.requests) * 100 : 0
    if (sample.release_role === 'CANDIDATE') {
      point.candidateErrorRate = Number(rate.toFixed(2))
      point.candidateP95 = sample.latency_p95_ms ?? undefined
    } else {
      point.stableErrorRate = Number(rate.toFixed(2))
      point.stableP95 = sample.latency_p95_ms ?? undefined
    }
    byTime.set(time, point)
  }
  return [...byTime.values()]
}

const AXIS = { stroke: '#64708a', fontSize: 11 }
const TOOLTIP_STYLE = {
  backgroundColor: '#121722',
  border: '1px solid #232b3b',
  borderRadius: 6,
  fontSize: 12,
}

export function HealthChart({
  samples, threshold,
}: {
  samples: HealthSample[]
  threshold: number
}) {
  if (samples.length === 0) {
    return (
      <EmptyState
        icon="◭"
        title="No health samples recorded"
        description="Samples appear once the rollout starts measuring traffic."
      />
    )
  }
  const points = toPoints(samples)

  return (
    <div className="space-y-6 p-4">
      <figure>
        <figcaption className="label mb-2">
          Error rate (%) — ceiling {(threshold * 100).toFixed(1)}%
        </figcaption>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid stroke="#232b3b" strokeDasharray="3 3" />
            <XAxis dataKey="time" {...AXIS} />
            <YAxis {...AXIS} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Line
              type="monotone" dataKey="stableErrorRate" name="Stable"
              stroke={STABLE} strokeWidth={2} dot={false} connectNulls
            />
            <Line
              type="monotone" dataKey="candidateErrorRate" name="Candidate"
              stroke={CANDIDATE} strokeWidth={2} dot={false} connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </figure>

      <figure>
        <figcaption className="label mb-2">p95 latency (ms)</figcaption>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid stroke="#232b3b" strokeDasharray="3 3" />
            <XAxis dataKey="time" {...AXIS} />
            <YAxis {...AXIS} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Line
              type="monotone" dataKey="stableP95" name="Stable"
              stroke={STABLE} strokeWidth={2} dot={false} connectNulls
            />
            <Line
              type="monotone" dataKey="candidateP95" name="Candidate"
              stroke={CANDIDATE} strokeWidth={2} dot={false} connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </figure>

      {/* A chart is not an accessible data source on its own. */}
      <details className="text-2xs text-ink-faint">
        <summary className="cursor-pointer hover:text-ink-muted">
          View the underlying measurements as a table
        </summary>
        <table className="mt-2 w-full">
          <caption className="sr-only">Health samples</caption>
          <thead>
            <tr>
              {['Time', 'Release', 'Version', 'Requests', 'Errors', 'p95 (ms)'].map(
                (column) => (
                  <th key={column} scope="col" className="th">
                    {column}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-border">
            {samples.map((sample) => (
              <tr key={sample.id}>
                <td className="td py-1.5">
                  {new Date(sample.observed_at).toLocaleTimeString()}
                </td>
                <td className="td py-1.5">{sample.release_role}</td>
                <td className="td py-1.5">{sample.version}</td>
                <td className="td py-1.5 tabular">{sample.requests}</td>
                <td className="td py-1.5 tabular">{sample.errors}</td>
                <td className="td py-1.5 tabular">
                  {sample.latency_p95_ms?.toFixed(1) ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}
