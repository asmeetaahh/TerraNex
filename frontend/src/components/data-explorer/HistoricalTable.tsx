import type { MetricDefinition, MetricPoint } from '../../lib/dataExplorer'
import { Card } from '../ui/Card'
import { shortDate } from '../../lib/format'

export function HistoricalTable({ metric, points }: { metric: MetricDefinition; points: MetricPoint[] }) {
  if (points.length === 0) {
    return (
      <Card className="p-6 text-sm text-[color:var(--color-ink-faint)]">
        No {metric.label.toLowerCase()} history is available for this farm yet.
      </Card>
    )
  }

  const rows = [...points].sort((a, b) => b.date.localeCompare(a.date))

  return (
    <Card className="overflow-hidden p-0">
      <div className="max-h-80 overflow-y-auto">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-[color:var(--color-surface)]">
            <tr className="border-b border-white/[0.06] text-[color:var(--color-ink-faint)]">
              <th className="px-4 py-2.5 font-medium">Date</th>
              <th className="px-4 py-2.5 font-medium">Metric</th>
              <th className="px-4 py-2.5 text-right font-medium">Value</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.date} className="border-b border-white/[0.04] last:border-0">
                <td className="px-4 py-2 text-[color:var(--color-ink-muted)]">{shortDate(row.date)}</td>
                <td className="px-4 py-2 text-[color:var(--color-ink-muted)]">{metric.shortLabel}</td>
                <td className="px-4 py-2 text-right font-medium tabular-nums text-[color:var(--color-ink)]">
                  {metric.id === 'ndvi' ? row.value.toFixed(3) : row.value.toFixed(1)}
                  {metric.unit && <span className="ml-1 font-normal text-[color:var(--color-ink-faint)]">{metric.unit}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
