import { Star } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import { Card } from '../ui/Card'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_TEXT_CLASS, titleCase, type Status } from '../../lib/status'

type Advisory = components['schemas']['Advisory']
type AdvisoryPriority = components['schemas']['AdvisoryPriority']

const PRIORITY_STATUS: Record<AdvisoryPriority, Status> = {
  critical: 'critical',
  high: 'serious',
  medium: 'warning',
  low: 'good',
}

const PRIORITY_ORDER: Record<AdvisoryPriority, number> = { critical: 0, high: 1, medium: 2, low: 3 }

export function AdvisoryOverview({ advisories, enums }: { advisories: Advisory[]; enums: EnumCatalog | null }) {
  const top = [...advisories].sort((a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority]).slice(0, 3)

  return (
    <Card className="p-5" id="advisory-overview">
      <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">AI Advisory</h2>
      <div className="mb-3 border-b border-white/[0.06] text-xs">
        <span className="inline-block border-b-2 border-lime-400 pb-2 font-medium text-lime-300">Today</span>
      </div>

      {top.length === 0 ? (
        <p className="py-4 text-sm text-[color:var(--color-ink-faint)]">No advisories for this run.</p>
      ) : (
        <div className="space-y-3">
          {top.map((advisory) => {
            const status = PRIORITY_STATUS[advisory.priority]
            const priorityLabel = findEnumLabel(enums, 'advisory_priority', advisory.priority) ?? titleCase(advisory.priority)
            return (
              <div key={advisory.id} className="flex gap-2.5 border-b border-white/[0.05] pb-3 last:border-0 last:pb-0">
                <Star size={14} strokeWidth={1.5} className="mt-0.5 shrink-0 fill-amber-300 text-amber-300" />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-[color:var(--color-ink)]">{advisory.title}</p>
                  <p className="mt-0.5 truncate text-xs text-[color:var(--color-ink-faint)]">{advisory.body}</p>
                  <div className="mt-1 flex items-center gap-2 text-[11px]">
                    <span className="text-[color:var(--color-ink-faint)]">Confidence {Math.round(advisory.confidence * 100)}%</span>
                    <span className={`font-medium ${STATUS_TEXT_CLASS[status]}`}>{priorityLabel} Priority</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
