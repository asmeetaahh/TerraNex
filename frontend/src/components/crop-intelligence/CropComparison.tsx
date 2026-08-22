import { GitCompare } from 'lucide-react'
import type { ReactNode } from 'react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'

type CropRecommendation = components['schemas']['CropRecommendation']

interface Row {
  label: string
  render: (rec: CropRecommendation) => ReactNode
}

const rows: Row[] = [
  {
    label: 'Suitability',
    render: (rec) => (
      <div className="flex items-center gap-2">
        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-white/[0.06]">
          <div className="h-full rounded-full bg-lime-400" style={{ width: `${Math.max(0, Math.min(100, rec.suitability_score))}%` }} />
        </div>
        <span className="font-semibold text-lime-400">{Math.round(rec.suitability_score)}%</span>
      </div>
    ),
  },
  { label: 'Category', render: (rec) => <span className="capitalize">{rec.category}</span> },
  {
    label: 'Water need',
    render: (rec) => (rec.water_requirement_mm != null ? `${Math.round(rec.water_requirement_mm)} mm/season` : '—'),
  },
  { label: 'Planting window', render: (rec) => rec.planting_window ?? '—' },
  {
    label: 'Top strength',
    render: (rec) => rec.strengths?.[0] ?? '—',
  },
]

/** A simple side-by-side comparison of the top recommended crops — readability over chart complexity. */
export function CropComparison({ recommendations }: { recommendations: CropRecommendation[] }) {
  const top = recommendations.slice(0, 3)
  if (top.length === 0) return null

  return (
    <section>
      <h2 className="mb-1 flex items-center gap-2 text-base font-semibold text-[color:var(--color-ink)]">
        <GitCompare size={16} strokeWidth={1.5} className="text-lime-400" />
        Crop Comparison
      </h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">The leading recommendations, side by side</p>

      <Card className="overflow-x-auto p-5">
        <div className="grid min-w-[560px] grid-cols-[140px_repeat(3,1fr)] gap-x-4 gap-y-3">
          <div />
          {top.map((rec) => (
            <div key={rec.crop_code} className="min-w-0">
              <p className="truncate text-sm font-semibold text-[color:var(--color-ink)]">{rec.crop_name}</p>
              {rec.is_current_crop && <p className="text-[10px] font-medium text-lime-400">Current crop</p>}
            </div>
          ))}

          {rows.map((row) => (
            <div key={row.label} className="contents">
              <div className="self-center text-xs font-medium text-[color:var(--color-ink-faint)]">{row.label}</div>
              {top.map((rec) => (
                <div key={rec.crop_code + row.label} className="self-center truncate text-xs text-[color:var(--color-ink-muted)]">
                  {row.render(rec)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </Card>
    </section>
  )
}
