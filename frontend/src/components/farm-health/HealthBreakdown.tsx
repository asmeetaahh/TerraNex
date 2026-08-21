import { Gauge } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { STATUS_COLOR, statusFromScoreBand } from '../../lib/status'

type ScoredFactor = components['schemas']['ScoredFactor']

/**
 * How each computed factor contributes to the composite Farm Health Score.
 * Sourced directly from `AnalysisRun.factors` (docs/API_CONTRACT.md — ScoredFactor)
 * — the same explainability data already used for the Dashboard's "Why this?" tags.
 */
export function HealthBreakdown({ factors }: { factors: ScoredFactor[] }) {
  if (factors.length === 0) return null

  const sorted = [...factors].sort((a, b) => b.weight - a.weight)

  return (
    <Card className="p-5">
      <h2 className="flex items-center gap-2 text-base font-semibold text-[color:var(--color-ink)]">
        <Gauge size={16} strokeWidth={1.5} className="text-lime-400" />
        Health Breakdown
      </h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">
        How each computed factor weighs into the overall Farm Health Score
      </p>

      <div className="space-y-4">
        {sorted.map((factor) => {
          const status = statusFromScoreBand(factor.band)
          const color = STATUS_COLOR[status]
          return (
            <div key={factor.key}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate text-sm font-medium text-[color:var(--color-ink)]">{factor.label}</span>
                <span className="shrink-0 text-xs text-[color:var(--color-ink-faint)]">
                  weight {Math.round(factor.weight * 100)}%
                </span>
              </div>
              <div className="mt-1.5 flex items-center gap-2.5">
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full transition-[width] duration-700 ease-out"
                    style={{
                      width: `${Math.max(0, Math.min(100, factor.score))}%`,
                      background: color,
                      boxShadow: `0 0 8px color-mix(in oklab, ${color} 60%, transparent)`,
                    }}
                  />
                </div>
                <span className="w-9 shrink-0 text-right text-xs font-semibold tabular-nums" style={{ color }}>
                  {Math.round(factor.score)}
                </span>
              </div>
              <p className="mt-1 truncate text-xs text-[color:var(--color-ink-faint)]">{factor.explanation}</p>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
