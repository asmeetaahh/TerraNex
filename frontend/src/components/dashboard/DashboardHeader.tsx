import { Bell, ChevronDown, Leaf, MapPin, RefreshCw } from 'lucide-react'
import type { Farm } from '../../api/farms'
import { greetingForHour } from '../../lib/format'

interface DashboardHeaderProps {
  farms: Farm[]
  selectedFarm: Farm | null
  onSelectFarm: (farmId: string) => void
}

export function DashboardHeader({ farms, selectedFarm, onSelectFarm }: DashboardHeaderProps) {
  const greeting = greetingForHour(new Date().getHours())

  return (
    <header className="mb-7 flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">
          {greeting}, Arjun!
          <Leaf size={20} strokeWidth={2} className="text-lime-400" />
        </h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">Here's what's happening on your farm today.</p>
      </div>

      <div className="flex items-center gap-2.5">
        {farms.length > 0 && selectedFarm && (
          <div className="relative">
            <div className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-[color:var(--color-surface-raised)] py-1.5 pr-8 pl-3">
              <MapPin size={15} strokeWidth={1.5} className="shrink-0 text-lime-400" />
              <div className="leading-tight">
                <p className="text-sm font-semibold text-[color:var(--color-ink)]">{selectedFarm.name}</p>
                <p className="text-[11px] text-[color:var(--color-ink-faint)]">
                  {[selectedFarm.region, selectedFarm.country_code].filter(Boolean).join(', ') || 'Location not set'}
                </p>
              </div>
            </div>
            <ChevronDown
              size={14}
              strokeWidth={1.5}
              className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[color:var(--color-ink-faint)]"
            />
            <select
              value={selectedFarm.id}
              onChange={(event) => onSelectFarm(event.target.value)}
              aria-label="Select farm"
              className="absolute inset-0 h-full w-full cursor-pointer appearance-none opacity-0"
            >
              {farms.map((farm) => (
                <option key={farm.id} value={farm.id}>
                  {farm.name}
                </option>
              ))}
            </select>
          </div>
        )}

        <button
          type="button"
          disabled
          title="Coming soon — POST /farms/{farm_id}/analysis is not implemented yet"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.02] text-[color:var(--color-ink-faint)] disabled:cursor-not-allowed"
        >
          <RefreshCw size={15} strokeWidth={1.5} />
        </button>

        <button
          type="button"
          disabled
          title="Notifications — coming soon"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.02] text-[color:var(--color-ink-faint)] disabled:cursor-not-allowed"
        >
          <Bell size={15} strokeWidth={1.5} />
        </button>
      </div>
    </header>
  )
}
