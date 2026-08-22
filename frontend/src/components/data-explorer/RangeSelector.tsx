export function RangeSelector({
  options,
  selected,
  onSelect,
  forward,
}: {
  options: number[]
  selected: number
  onSelect: (days: number) => void
  /** True for a forward-looking forecast window, false for a real backward-looking history window. */
  forward: boolean
}) {
  if (options.length === 0) return null

  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] text-[color:var(--color-ink-faint)]">{forward ? 'Forecast window' : 'Range'}</span>
      <div className="flex gap-1 rounded-full border border-white/[0.08] p-0.5">
        {options.map((days) => {
          const active = days === selected
          return (
            <button
              key={days}
              type="button"
              onClick={() => onSelect(days)}
              className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${
                active ? 'bg-lime-500/15 text-lime-300' : 'text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]'
              }`}
            >
              {days}d
            </button>
          )
        })}
      </div>
    </div>
  )
}
