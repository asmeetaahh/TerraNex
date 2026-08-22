import { useId } from 'react'
import type { MetricPoint } from '../../lib/dataExplorer'

/**
 * Generic hand-rolled SVG line chart — same technique as the Crop
 * Intelligence page's `NdviTrendChart` and the dashboard `Sparkline`
 * (deliberately not pulling recharts back into the bundle). Parameterized so
 * it can plot any of the Data Explorer's four metrics, with a unique
 * gradient id via `useId` since more than one chart can exist per page.
 */
export function MetricChart({
  points,
  color,
  width = 640,
  height = 160,
}: {
  points: MetricPoint[]
  color: string
  width?: number
  height?: number
}) {
  const gradientId = useId()

  if (points.length < 2) return null

  const values = points.map((p) => p.value)
  const rawMin = Math.min(...values)
  const rawMax = Math.max(...values)
  const span = rawMax - rawMin || 1
  const min = rawMin - span * 0.1
  const max = rawMax + span * 0.1

  const pad = 8
  const usableW = width - pad * 2
  const usableH = height - pad * 2

  const coords = points.map((point, index) => {
    const x = pad + (index / (points.length - 1)) * usableW
    const y = pad + usableH - ((point.value - min) / (max - min || 1)) * usableH
    return `${x},${y}`
  })

  const areaPoints = `${coords[0]} ${coords.join(' ')} ${coords[coords.length - 1].split(',')[0]},${pad + usableH}`
  const last = coords[coords.length - 1].split(',')

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="overflow-visible">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.28} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill={`url(#${gradientId})`} />
      <polyline
        points={coords.join(' ')}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ filter: `drop-shadow(0 0 4px color-mix(in oklab, ${color} 55%, transparent))` }}
      />
      <circle cx={last[0]} cy={last[1]} r={3.5} fill={color} />
    </svg>
  )
}
