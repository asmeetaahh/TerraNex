import { METRICS, type MetricId } from '../../lib/dataExplorer'

export function MetricSelector({ selected, onSelect }: { selected: MetricId; onSelect: (id: MetricId) => void }) {
  return (
    <div className="flex flex-wrap gap-2" role="tablist" aria-label="Metric">
      {METRICS.map((metric) => {
        const active = metric.id === selected
        return (
          <button
            key={metric.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onSelect(metric.id)}
            className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
              active
                ? 'border-lime-400/40 bg-lime-500/10 text-lime-300'
                : 'border-white/[0.08] text-[color:var(--color-ink-muted)] hover:bg-white/[0.04] hover:text-[color:var(--color-ink)]'
            }`}
          >
            {metric.label}
          </button>
        )
      })}
    </div>
  )
}
