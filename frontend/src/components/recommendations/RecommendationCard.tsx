import { Clock, Leaf, ListChecks, Sparkles } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import { Card } from '../ui/Card'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_BG_CLASS, STATUS_TEXT_CLASS, titleCase, type Status } from '../../lib/status'

type Advisory = components['schemas']['Advisory']
type AdvisoryPriority = components['schemas']['AdvisoryPriority']
type RegenerativeRecommendation = components['schemas']['RegenerativeRecommendation']

const PRIORITY_STATUS: Record<AdvisoryPriority, Status> = {
  critical: 'critical',
  high: 'serious',
  medium: 'warning',
  low: 'good',
}

function relevanceStatus(score: number): Status {
  if (score >= 70) return 'good'
  if (score >= 45) return 'warning'
  return 'serious'
}

type RecommendationItem =
  | { kind: 'advisory'; data: Advisory }
  | { kind: 'regenerative'; data: RegenerativeRecommendation }

export function RecommendationCard({ item, enums }: { item: RecommendationItem; enums: EnumCatalog | null }) {
  if (item.kind === 'advisory') {
    const advisory = item.data
    const status = PRIORITY_STATUS[advisory.priority]
    const priorityLabel = findEnumLabel(enums, 'advisory_priority', advisory.priority) ?? titleCase(advisory.priority)
    const categoryLabel = findEnumLabel(enums, 'advisory_category', advisory.category) ?? titleCase(advisory.category)

    return (
      <Card className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="flex items-center gap-1 rounded-full border border-white/[0.08] px-2 py-0.5 text-[10px] font-medium text-[color:var(--color-ink-faint)]">
            <Sparkles size={10} strokeWidth={2} />
            Advisory
          </span>
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${STATUS_BG_CLASS[status]} ${STATUS_TEXT_CLASS[status]}`}>
            {priorityLabel}
          </span>
          <span className="text-[10px] text-[color:var(--color-ink-faint)]">{categoryLabel}</span>
        </div>

        <p className="text-sm font-medium text-[color:var(--color-ink)]">{advisory.title}</p>
        <p className="text-xs text-[color:var(--color-ink-muted)]">{advisory.body}</p>

        <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
          <p className="text-[10px] font-semibold tracking-wide text-[color:var(--color-ink-faint)] uppercase">Why this?</p>
          <p className="mt-0.5 text-xs text-[color:var(--color-ink-muted)]">{advisory.rationale}</p>
        </div>

        <div className="mt-auto flex items-center justify-between border-t border-white/[0.06] pt-2.5 text-[11px] text-[color:var(--color-ink-faint)]">
          {advisory.action_window ? (
            <span className="flex items-center gap-1">
              <Clock size={11} strokeWidth={1.5} />
              {advisory.action_window}
            </span>
          ) : (
            <span />
          )}
          <span>{Math.round(advisory.confidence * 100)}% confidence</span>
        </div>
      </Card>
    )
  }

  const rec = item.data
  const status = relevanceStatus(rec.relevance_score)

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="flex items-center gap-1 rounded-full border border-white/[0.08] px-2 py-0.5 text-[10px] font-medium text-[color:var(--color-ink-faint)]">
          <Leaf size={10} strokeWidth={2} />
          Regenerative Practice
        </span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_BG_CLASS[status]} ${STATUS_TEXT_CLASS[status]}`}>
          {Math.round(rec.relevance_score)}% relevance
        </span>
      </div>

      <p className="text-sm font-medium text-[color:var(--color-ink)]">{rec.practice_name}</p>
      <p className="text-xs text-[color:var(--color-ink-muted)]">{rec.description}</p>

      <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
        <p className="text-[10px] font-semibold tracking-wide text-[color:var(--color-ink-faint)] uppercase">Why this?</p>
        <p className="mt-0.5 text-xs text-[color:var(--color-ink-muted)]">{rec.rationale}</p>
      </div>

      {rec.expected_benefits && rec.expected_benefits.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {rec.expected_benefits.slice(0, 3).map((benefit) => (
            <span key={benefit} className="rounded-full border border-white/[0.08] px-2 py-0.5 text-[10px] text-[color:var(--color-ink-muted)]">
              {benefit}
            </span>
          ))}
        </div>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-white/[0.06] pt-2.5 text-[11px] text-[color:var(--color-ink-faint)]">
        {rec.time_to_benefit && (
          <span className="flex items-center gap-1">
            <Clock size={11} strokeWidth={1.5} />
            {rec.time_to_benefit}
          </span>
        )}
        {rec.effort_level && (
          <span className="flex items-center gap-1 capitalize">
            <ListChecks size={11} strokeWidth={1.5} />
            {rec.effort_level} effort
          </span>
        )}
      </div>
    </Card>
  )
}

export type { RecommendationItem }
