import { CalendarClock, Shovel, TreeDeciduous } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { buildRegenerativeRoadmap } from '../../lib/regenerative'

type RegenerativeRecommendation = components['schemas']['RegenerativeRecommendation']

const COLUMNS = [
  { key: 'now', icon: Shovel, title: 'Now', timeframe: 'Ready to start today', accent: 'var(--color-status-warning)' },
  { key: 'thisSeason', icon: CalendarClock, title: 'This Season', timeframe: 'Benefits within a season or two', accent: 'var(--color-lime-400)' },
  { key: 'longTerm', icon: TreeDeciduous, title: 'Long Term', timeframe: 'Multi-season or multi-year payoff', accent: 'var(--color-status-good)' },
] as const

/** Buckets the SAME practice cards shown above by their real `time_to_benefit` field — no second source of truth. */
export function RegenerationRoadmap({ practices }: { practices: RegenerativeRecommendation[] }) {
  if (practices.length === 0) return null

  const roadmap = buildRegenerativeRoadmap(practices)

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">Regeneration Roadmap</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">The same practices above, organized by when their benefits land</p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {COLUMNS.map((column) => {
          const Icon = column.icon
          const items = roadmap[column.key]
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
                <div className="space-y-2">
                  {items.map((practice) => (
                    <div key={practice.practice_code} className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                      <p className="text-xs font-medium text-[color:var(--color-ink)]">{practice.practice_name}</p>
                      {practice.time_to_benefit && (
                        <p className="mt-0.5 text-[11px] text-[color:var(--color-ink-faint)]">{practice.time_to_benefit}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )
        })}
      </div>
    </section>
  )
}
