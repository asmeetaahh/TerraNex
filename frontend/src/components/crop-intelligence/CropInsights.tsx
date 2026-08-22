import { Sparkles, Target, TriangleAlert } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'

type Advisory = components['schemas']['Advisory']
type CropHealth = components['schemas']['CropHealth']
type CropRecommendation = components['schemas']['CropRecommendation']

interface Insight {
  key: string
  icon: typeof Sparkles
  tone: 'warning' | 'good'
  title: string
  body: string
}

/** The 2-3 most useful crop-related takeaways, drawn from real advisory, stress-indicator, and recommendation data — no new data invented. */
export function CropInsights({
  advisories,
  cropHealth,
  topRecommendation,
}: {
  advisories: Advisory[]
  cropHealth: CropHealth
  topRecommendation: CropRecommendation | null
}) {
  const insights: Insight[] = []

  if (cropHealth.stress_indicators && cropHealth.stress_indicators.length > 0) {
    insights.push({
      key: 'stress',
      icon: TriangleAlert,
      tone: 'warning',
      title: 'Stress signal detected',
      body: cropHealth.stress_indicators[0],
    })
  }

  const topAdvisory = advisories.find((a) => a.category === 'planting' || a.category === 'nutrient') ?? advisories[0]
  if (topAdvisory) {
    insights.push({
      key: 'advisory',
      icon: Sparkles,
      tone: 'warning',
      title: topAdvisory.title,
      body: topAdvisory.rationale,
    })
  }

  if (topRecommendation && !topRecommendation.is_current_crop) {
    insights.push({
      key: 'recommendation',
      icon: Target,
      tone: 'good',
      title: `Consider ${topRecommendation.crop_name} next season`,
      body: topRecommendation.rationale,
    })
  }

  if (insights.length === 0) return null

  return (
    <Card className="p-5">
      <h2 className="text-base font-semibold text-[color:var(--color-ink)]">Actionable Insights</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">What matters most for this crop right now</p>

      <div className="space-y-3">
        {insights.map((insight) => {
          const Icon = insight.icon
          const toneClass = insight.tone === 'warning' ? 'bg-amber-400/10 text-amber-300' : 'bg-lime-500/10 text-lime-400'
          return (
            <div key={insight.key} className="flex gap-3 border-b border-white/[0.05] pb-3 last:border-0 last:pb-0">
              <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${toneClass}`}>
                <Icon size={13} strokeWidth={1.5} />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-[color:var(--color-ink)]">{insight.title}</p>
                <p className="mt-0.5 text-xs text-[color:var(--color-ink-muted)]">{insight.body}</p>
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
