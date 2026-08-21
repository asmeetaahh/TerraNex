import { CalendarClock, CalendarRange, Zap } from 'lucide-react'
import type { ReactNode } from 'react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'

type Advisory = components['schemas']['Advisory']
type RegenerativeRecommendation = components['schemas']['RegenerativeRecommendation']

interface PlanColumn {
  key: string
  icon: typeof Zap
  title: string
  timeframe: string
  accent: string
}

const COLUMNS: PlanColumn[] = [
  { key: 'immediate', icon: Zap, title: 'Immediate', timeframe: 'Critical & high priority', accent: 'var(--color-status-critical)' },
  { key: 'this_week', icon: CalendarClock, title: 'This Week', timeframe: 'Medium priority', accent: 'var(--color-status-warning)' },
  { key: 'longer_term', icon: CalendarRange, title: 'Longer Term', timeframe: 'Low priority & regenerative practices', accent: 'var(--color-lime-400)' },
]

/**
 * Buckets the SAME advisory/regenerative data already shown elsewhere on this
 * page by urgency — no second source of truth, just a different lens on it.
 */
export function ActionPlan({
  advisories,
  regenerative,
}: {
  advisories: Advisory[]
  regenerative: RegenerativeRecommendation[]
}) {
  const immediate = advisories.filter((a) => a.priority === 'critical' || a.priority === 'high')
  const thisWeek = advisories.filter((a) => a.priority === 'medium')
  const lowPriority = advisories.filter((a) => a.priority === 'low')

  const buckets: Record<string, ReactNode[]> = {
    immediate: immediate.map((a) => (
      <ActionItem key={a.id} title={a.title} timing={a.action_window} />
    )),
    this_week: thisWeek.map((a) => (
      <ActionItem key={a.id} title={a.title} timing={a.action_window} />
    )),
    longer_term: [
      ...lowPriority.map((a) => <ActionItem key={a.id} title={a.title} timing={a.action_window} />),
      ...regenerative.map((r) => <ActionItem key={r.practice_code} title={r.practice_name} timing={r.time_to_benefit} />),
    ],
  }

  if (immediate.length + thisWeek.length + lowPriority.length + regenerative.length === 0) return null

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">Action Plan</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">
        The same advisories and practices above, organized by when to act
      </p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {COLUMNS.map((column) => {
          const Icon = column.icon
          const items = buckets[column.key]
          return (
            <Card key={column.key} className="p-4">
              <div className="mb-1 flex items-center gap-2">
                <span
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
                  style={{ backgroundColor: `color-mix(in oklab, ${column.accent} 15%, transparent)`, color: column.accent }}
                >
                  <Icon size={13} strokeWidth={1.5} />
                </span>
                <p className="text-sm font-semibold text-[color:var(--color-ink)]">{column.title}</p>
              </div>
              <p className="mb-3 text-[11px] text-[color:var(--color-ink-faint)]">{column.timeframe}</p>

              {items.length === 0 ? (
                <p className="text-xs text-[color:var(--color-ink-faint)]">Nothing in this window.</p>
              ) : (
                <div className="space-y-2">{items}</div>
              )}
            </Card>
          )
        })}
      </div>
    </section>
  )
}

function ActionItem({ title, timing }: { title: string; timing?: string | null }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2">
      <p className="text-xs font-medium text-[color:var(--color-ink)]">{title}</p>
      {timing && <p className="mt-0.5 text-[11px] text-[color:var(--color-ink-faint)]">{timing}</p>}
    </div>
  )
}
