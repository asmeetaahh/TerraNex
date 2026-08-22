import { Minus, TrendingDown, TrendingUp } from 'lucide-react'
import type { MetricDefinition, MetricSummary } from '../../lib/dataExplorer'
import { Card } from '../ui/Card'

const DIRECTION_ICON = { up: TrendingUp, down: TrendingDown, flat: Minus } as const

export function MetricSummaryCard({ metric, summary }: { metric: MetricDefinition; summary: MetricSummary }) {
  const DirectionIcon = summary.changeDirection ? DIRECTION_ICON[summary.changeDirection] : null

  return (
    <Card className="flex flex-col gap-3 p-4 sm:min-w-[220px]">
      <p className="text-xs font-medium tracking-wide text-[color:var(--color-ink-muted)] uppercase">Current {metric.shortLabel}</p>

      {summary.currentValue == null ? (
        <p className="text-sm text-[color:var(--color-ink-faint)]">Not available for this farm yet.</p>
      ) : (
        <>
          <p className="text-3xl font-semibold tabular-nums" style={{ color: metric.color }}>
            {metric.id === 'ndvi' ? summary.currentValue.toFixed(2) : Math.round(summary.currentValue * 10) / 10}
            {metric.unit && <span className="ml-1 text-base font-normal text-[color:var(--color-ink-faint)]">{metric.unit}</span>}
          </p>

          {summary.changeText ? (
            <p className="flex items-center gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
              {DirectionIcon && (
                <DirectionIcon
                  size={13}
                  strokeWidth={2}
                  style={{ color: summary.changeDirection === 'flat' ? 'var(--color-ink-faint)' : metric.color }}
                />
              )}
              {summary.changeText}
            </p>
          ) : (
            <p className="text-xs text-[color:var(--color-ink-faint)]">
              {metric.kind === 'single' ? 'Only a single measurement is available — no trend yet.' : 'Not enough data for a trend yet.'}
            </p>
          )}
        </>
      )}
    </Card>
  )
}
