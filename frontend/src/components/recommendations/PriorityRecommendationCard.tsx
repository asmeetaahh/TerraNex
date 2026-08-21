import { Clock, Zap } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import { Card } from '../ui/Card'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_COLOR, STATUS_TEXT_CLASS, titleCase, type Status } from '../../lib/status'

type Advisory = components['schemas']['Advisory']
type AdvisoryPriority = components['schemas']['AdvisoryPriority']

const PRIORITY_STATUS: Record<AdvisoryPriority, Status> = {
  critical: 'critical',
  high: 'serious',
  medium: 'warning',
  low: 'good',
}

/** The single highest-priority advisory, given hero treatment — same visual weight as the Farm Health page's Overall Health card. */
export function PriorityRecommendationCard({ advisory, enums }: { advisory: Advisory; enums: EnumCatalog | null }) {
  const status = PRIORITY_STATUS[advisory.priority]
  const priorityLabel = findEnumLabel(enums, 'advisory_priority', advisory.priority) ?? titleCase(advisory.priority)
  const categoryLabel = findEnumLabel(enums, 'advisory_category', advisory.category) ?? titleCase(advisory.category)
  const color = STATUS_COLOR[status]

  return (
    <Card glow className="p-6 sm:p-7">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold tracking-wide uppercase"
          style={{ backgroundColor: `color-mix(in oklab, ${color} 15%, transparent)`, color }}
        >
          <Zap size={12} strokeWidth={2.5} />
          {priorityLabel} priority
        </span>
        <span className="rounded-full border border-white/[0.08] px-2.5 py-1 text-xs text-[color:var(--color-ink-muted)]">
          {categoryLabel}
        </span>
        {advisory.action_window && (
          <span className="flex items-center gap-1 text-xs text-[color:var(--color-ink-faint)]">
            <Clock size={12} strokeWidth={1.5} />
            {advisory.action_window}
          </span>
        )}
      </div>

      <h2 className="mt-3 text-xl font-semibold text-[color:var(--color-ink)] sm:text-2xl">{advisory.title}</h2>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--color-ink-muted)]">{advisory.body}</p>

      <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
        <p className="text-xs font-semibold tracking-wide text-[color:var(--color-ink-muted)] uppercase">Why this recommendation?</p>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">{advisory.rationale}</p>
      </div>

      <div className="mt-4 flex items-center gap-2.5">
        <span className="text-xs text-[color:var(--color-ink-faint)]">Confidence</span>
        <div className="h-1.5 w-32 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full rounded-full"
            style={{ width: `${Math.round(advisory.confidence * 100)}%`, background: color }}
          />
        </div>
        <span className={`text-xs font-semibold ${STATUS_TEXT_CLASS[status]}`}>{Math.round(advisory.confidence * 100)}%</span>
      </div>
    </Card>
  )
}
