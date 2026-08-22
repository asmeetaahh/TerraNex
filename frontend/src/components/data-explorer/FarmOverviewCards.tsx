import { METRICS, type MetricId, type MetricSummary } from '../../lib/dataExplorer'
import { Card } from '../ui/Card'

/** Quick at-a-glance snapshot across all four metrics — independent of whichever one is selected below. */
export function FarmOverviewCards({ summaries }: { summaries: Record<MetricId, MetricSummary> }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {METRICS.map((metric) => {
        const summary = summaries[metric.id]
        return (
          <Card key={metric.id} className="flex min-w-0 flex-col gap-1.5 p-4">
            <span className="truncate text-xs font-medium text-[color:var(--color-ink-muted)]">{metric.shortLabel}</span>
            {summary.currentValue == null ? (
              <span className="text-sm text-[color:var(--color-ink-faint)]">Not tracked</span>
            ) : (
              <span className="truncate text-xl font-semibold tabular-nums sm:text-2xl" style={{ color: metric.color }}>
                {metric.id === 'ndvi' ? summary.currentValue.toFixed(2) : Math.round(summary.currentValue * 10) / 10}
                {metric.unit && <span className="ml-1 text-xs font-normal text-[color:var(--color-ink-faint)]">{metric.unit}</span>}
              </span>
            )}
          </Card>
        )
      })}
    </div>
  )
}
