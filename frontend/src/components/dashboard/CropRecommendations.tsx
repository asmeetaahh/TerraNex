import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'

type CropRecommendation = components['schemas']['CropRecommendation']

export function CropRecommendations({ recommendations }: { recommendations: CropRecommendation[] }) {
  return (
    <Card className="p-5" id="crop-recommendations">
      <h2 className="text-base font-semibold text-[color:var(--color-ink)]">Crop Recommendations</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">Best crops for your next season</p>

      {recommendations.length === 0 ? (
        <p className="py-4 text-sm text-[color:var(--color-ink-faint)]">No recommendations for this run.</p>
      ) : (
        <div className="divide-y divide-white/[0.05]">
          {recommendations.map((rec) => (
            <div key={rec.crop_code} className="flex gap-3 py-3 first:pt-0 last:pb-0">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-lime-500/10 text-[11px] font-semibold text-lime-300">
                {rec.rank}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="min-w-0 flex-1 truncate text-sm font-medium text-[color:var(--color-ink)]">{rec.crop_name}</p>
                  <span className="shrink-0 text-sm font-semibold text-lime-400">{Math.round(rec.suitability_score)}%</span>
                </div>
                <div className="flex items-baseline justify-between gap-2">
                  <p className="min-w-0 flex-1 truncate text-xs text-[color:var(--color-ink-faint)]">{rec.rationale}</p>
                  {rec.is_current_crop && (
                    <span className="shrink-0 text-[10px] font-medium text-lime-400">Current</span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
