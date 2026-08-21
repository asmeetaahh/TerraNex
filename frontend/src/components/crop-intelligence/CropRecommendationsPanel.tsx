import { Sprout } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'

type CropRecommendation = components['schemas']['CropRecommendation']

/** A richer, ranked view of `AnalysisRun.crop_recommendations` — same data as the Dashboard's compact list, expanded with strengths and planting windows. */
export function CropRecommendationsPanel({ recommendations }: { recommendations: CropRecommendation[] }) {
  return (
    <section>
      <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">Crop Recommendations</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">Ranked by suitability against this farm's soil and climate</p>

      {recommendations.length === 0 ? (
        <Card className="p-6 text-sm text-[color:var(--color-ink-faint)]">No recommendations for this run.</Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {recommendations.map((rec) => (
            <Card key={rec.crop_code} className="flex flex-col gap-3 p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-lime-500/10 text-xs font-semibold text-lime-300">
                    {rec.rank}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-[color:var(--color-ink)]">{rec.crop_name}</p>
                    <p className="truncate text-[11px] text-[color:var(--color-ink-faint)] capitalize">{rec.category}</p>
                  </div>
                </div>
                {rec.is_current_crop && (
                  <span className="flex shrink-0 items-center gap-1 rounded-full bg-lime-500/10 px-2 py-0.5 text-[10px] font-medium text-lime-300">
                    <Sprout size={10} strokeWidth={2} />
                    Current
                  </span>
                )}
              </div>

              <div className="flex items-center gap-2">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-lime-400"
                    style={{ width: `${Math.max(0, Math.min(100, rec.suitability_score))}%` }}
                  />
                </div>
                <span className="text-xs font-semibold tabular-nums text-lime-400">{Math.round(rec.suitability_score)}%</span>
              </div>

              <p className="text-xs leading-relaxed text-[color:var(--color-ink-muted)]">{rec.rationale}</p>

              {rec.strengths && rec.strengths.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {rec.strengths.slice(0, 2).map((strength) => (
                    <span
                      key={strength}
                      className="rounded-full border border-white/[0.06] px-2 py-0.5 text-[10px] text-[color:var(--color-ink-muted)]"
                    >
                      {strength}
                    </span>
                  ))}
                </div>
              )}

              {rec.planting_window && (
                <p className="mt-auto border-t border-white/[0.06] pt-2 text-[11px] text-[color:var(--color-ink-faint)]">
                  Plant: {rec.planting_window}
                </p>
              )}
            </Card>
          ))}
        </div>
      )}
    </section>
  )
}
