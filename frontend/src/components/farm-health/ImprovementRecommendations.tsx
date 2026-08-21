import { Lightbulb } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'

type RegenerativeRecommendation = components['schemas']['RegenerativeRecommendation']

/** Practices that would improve farm health, sourced from `AnalysisRun.regenerative_recommendations`. */
export function ImprovementRecommendations({ recommendations }: { recommendations: RegenerativeRecommendation[] }) {
  return (
    <Card className="p-5">
      <h2 className="flex items-center gap-2 text-base font-semibold text-[color:var(--color-ink)]">
        <Lightbulb size={16} strokeWidth={1.5} className="text-lime-400" />
        Improve Your Farm Health
      </h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">Practices ranked by expected impact</p>

      {recommendations.length === 0 ? (
        <p className="py-4 text-sm text-[color:var(--color-ink-faint)]">No recommendations for this run.</p>
      ) : (
        <div className="divide-y divide-white/[0.05]">
          {recommendations.map((rec) => (
            <div key={rec.practice_code} className="flex gap-3 py-3 first:pt-0 last:pb-0">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-lime-500/10 text-[11px] font-semibold text-lime-300">
                {rec.rank}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="min-w-0 flex-1 truncate text-sm font-medium text-[color:var(--color-ink)]">{rec.practice_name}</p>
                  <span className="shrink-0 text-sm font-semibold text-lime-400">{Math.round(rec.relevance_score)}%</span>
                </div>
                <p className="truncate text-xs text-[color:var(--color-ink-faint)]">{rec.rationale}</p>
                {rec.expected_benefits && rec.expected_benefits.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {rec.expected_benefits.slice(0, 2).map((benefit) => (
                      <span
                        key={benefit}
                        className="rounded-full border border-white/[0.08] px-2 py-0.5 text-[10px] text-[color:var(--color-ink-muted)]"
                      >
                        {benefit}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
