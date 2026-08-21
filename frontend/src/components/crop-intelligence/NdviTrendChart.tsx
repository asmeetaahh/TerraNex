import type { components } from '../../api/types.gen'

type VegetationPoint = components['schemas']['VegetationPoint']

/**
 * A small hand-rolled SVG line chart — deliberately not pulling recharts back
 * in for one chart on one page (it was dropped from the bundle when the
 * Dashboard's progress chart was retired). Same visual technique as `Sparkline`.
 */
export function NdviTrendChart({ series, width = 520, height = 120 }: { series: VegetationPoint[]; width?: number; height?: number }) {
  const values = series.map((point) => point.ndvi).filter((v): v is number => v != null)
  if (values.length < 2) return null

  const min = Math.min(...values, 0.3)
  const max = Math.max(...values, 0.9)
  const pad = 8
  const usableW = width - pad * 2
  const usableH = height - pad * 2

  const points = series
    .map((point, index) => {
      if (point.ndvi == null) return null
      const x = pad + (index / (series.length - 1)) * usableW
      const y = pad + usableH - ((point.ndvi - min) / (max - min || 1)) * usableH
      return `${x},${y}`
    })
    .filter((p): p is string => p !== null)

  const areaPoints = `${points[0]} ${points.join(' ')} ${points[points.length - 1]?.split(',')[0]},${pad + usableH}`

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="overflow-visible">
      <defs>
        <linearGradient id="ndviFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--color-lime-400)" stopOpacity={0.3} />
          <stop offset="100%" stopColor="var(--color-lime-400)" stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill="url(#ndviFill)" />
      <polyline
        points={points.join(' ')}
        fill="none"
        stroke="var(--color-lime-400)"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ filter: 'drop-shadow(0 0 4px color-mix(in oklab, var(--color-lime-400) 55%, transparent))' }}
      />
      {points.length > 0 && (
        <circle cx={points[points.length - 1].split(',')[0]} cy={points[points.length - 1].split(',')[1]} r={3.5} fill="var(--color-lime-400)" />
      )}
    </svg>
  )
}
