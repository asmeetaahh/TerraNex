import { Clock, Droplets, Layers, Leaf, ListChecks } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { STATUS_BG_CLASS, STATUS_TEXT_CLASS, type Status } from '../../lib/status'

type RegenerativeRecommendation = components['schemas']['RegenerativeRecommendation']

function relevanceStatus(score: number): Status {
  if (score >= 70) return 'good'
  if (score >= 45) return 'warning'
  return 'serious'
}

/**
 * Practice card for the Regenerative page only — answers "how does this
 * improve my farm over time?" using the `soil_carbon_impact` and
 * `water_retention_impact` fields (unused elsewhere in the app), not the
 * "what should I do now?" framing `RecommendationCard` uses on the
 * Recommendations page.
 */
export function RegenerativePracticeCard({ practice }: { practice: RegenerativeRecommendation }) {
  const status = relevanceStatus(practice.relevance_score)

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="flex items-center gap-1 rounded-full border border-white/[0.08] px-2 py-0.5 text-[10px] font-medium text-[color:var(--color-ink-faint)]">
          <Leaf size={10} strokeWidth={2} />
          Regenerative Practice
        </span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_BG_CLASS[status]} ${STATUS_TEXT_CLASS[status]}`}>
          {Math.round(practice.relevance_score)}% relevance
        </span>
      </div>

      <div>
        <p className="text-sm font-medium text-[color:var(--color-ink)]">{practice.practice_name}</p>
        <p className="mt-0.5 text-xs text-[color:var(--color-ink-muted)]">{practice.description}</p>
      </div>

      <div className="space-y-2 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
        {practice.soil_carbon_impact && (
          <div className="flex items-start gap-2">
            <Layers size={12} strokeWidth={2} className="mt-0.5 shrink-0 text-lime-400" />
            <p className="text-xs text-[color:var(--color-ink-muted)]">
              <span className="font-medium text-[color:var(--color-ink)]">Soil carbon: </span>
              {practice.soil_carbon_impact}
            </p>
          </div>
        )}
        {practice.water_retention_impact && (
          <div className="flex items-start gap-2">
            <Droplets size={12} strokeWidth={2} className="mt-0.5 shrink-0 text-sky-300" />
            <p className="text-xs text-[color:var(--color-ink-muted)]">
              <span className="font-medium text-[color:var(--color-ink)]">Water retention: </span>
              {practice.water_retention_impact}
            </p>
          </div>
        )}
      </div>

      {practice.expected_benefits && practice.expected_benefits.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold tracking-wide text-[color:var(--color-ink-faint)] uppercase">
            Ecosystem &amp; soil benefits
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {practice.expected_benefits.map((benefit) => (
              <span key={benefit} className="rounded-full border border-white/[0.08] px-2 py-0.5 text-[10px] text-[color:var(--color-ink-muted)]">
                {benefit}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-white/[0.06] pt-2.5 text-[11px] text-[color:var(--color-ink-faint)]">
        {practice.time_to_benefit && (
          <span className="flex items-center gap-1">
            <Clock size={11} strokeWidth={1.5} />
            Payoff: {practice.time_to_benefit}
          </span>
        )}
        {practice.effort_level && (
          <span className="flex items-center gap-1 capitalize">
            <ListChecks size={11} strokeWidth={1.5} />
            {practice.effort_level} effort
          </span>
        )}
      </div>
    </Card>
  )
}
