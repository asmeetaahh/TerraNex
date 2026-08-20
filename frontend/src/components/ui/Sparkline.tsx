import { STATUS_COLOR, type Status } from '../../lib/status'

/**
 * A small decorative trend line seeded from a single score — not a claim
 * about real history (we don't have per-factor history in the contract).
 * Purely a visual echo of the reference template's mini-charts.
 */
export function Sparkline({ score, status, width = 96, height = 28 }: { score: number; status: Status; width?: number; height?: number }) {
  const seed = Math.round(score)
  const wobble = [0, -6, 3, -3, 5, 0].map((w, i) => Math.max(4, Math.min(96, seed + w - (5 - i) * 1.5)))
  const points = wobble.map((v, i) => {
    const x = (i / (wobble.length - 1)) * width
    const y = height - (v / 100) * height
    return `${x},${y}`
  })
  const color = STATUS_COLOR[status]

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} fill="none" aria-hidden="true">
      <polyline
        points={points.join(' ')}
        stroke={color}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ filter: `drop-shadow(0 0 3px color-mix(in oklab, ${color} 60%, transparent))` }}
      />
    </svg>
  )
}
