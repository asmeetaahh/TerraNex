import { ShieldAlert, TriangleAlert } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import { Card } from '../ui/Card'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_BG_CLASS, STATUS_TEXT_CLASS, titleCase, type Status } from '../../lib/status'

type Advisory = components['schemas']['Advisory']
type AdvisoryPriority = components['schemas']['AdvisoryPriority']

const PRIORITY_STATUS: Record<AdvisoryPriority, Status> = {
  critical: 'critical',
  high: 'serious',
  medium: 'warning',
  low: 'good',
}

const PRIORITY_ORDER: Record<AdvisoryPriority, number> = { critical: 0, high: 1, medium: 2, low: 3 }

/** The most urgent items from the latest run's advisories, ranked by priority — the "what needs attention" view. */
export function KeyConcerns({ advisories, enums }: { advisories: Advisory[]; enums: EnumCatalog | null }) {
  const ranked = [...advisories].sort((a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority]).slice(0, 5)

  return (
    <Card className="p-5">
      <h2 className="flex items-center gap-2 text-base font-semibold text-[color:var(--color-ink)]">
        <ShieldAlert size={16} strokeWidth={1.5} className="text-lime-400" />
        Key Concerns
      </h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">Current issues, highest priority first</p>

      {ranked.length === 0 ? (
        <p className="py-4 text-sm text-[color:var(--color-ink-faint)]">No concerns flagged for this run.</p>
      ) : (
        <div className="space-y-3">
          {ranked.map((advisory) => {
            const status = PRIORITY_STATUS[advisory.priority]
            const priorityLabel = findEnumLabel(enums, 'advisory_priority', advisory.priority) ?? titleCase(advisory.priority)
            return (
              <div key={advisory.id} className="flex gap-2.5 border-b border-white/[0.05] pb-3 last:border-0 last:pb-0">
                <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${STATUS_BG_CLASS[status]}`}>
                  <TriangleAlert size={12} strokeWidth={2} className={STATUS_TEXT_CLASS[status]} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <p className="text-sm font-medium text-[color:var(--color-ink)]">{advisory.title}</p>
                    <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${STATUS_BG_CLASS[status]} ${STATUS_TEXT_CLASS[status]}`}>
                      {priorityLabel}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-[color:var(--color-ink-muted)]">{advisory.rationale}</p>
                  {advisory.action_window && (
                    <p className="mt-1 text-[11px] text-[color:var(--color-ink-faint)]">Act: {advisory.action_window}</p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
