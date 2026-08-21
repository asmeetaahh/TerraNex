import { Bug, CloudSun, Droplets, Leaf, Sprout } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { STATUS_COLOR, statusFromScoreBand } from '../../lib/status'

type ScoredFactor = components['schemas']['ScoredFactor']

// Backend and preview-fixture factor keys have used slightly different names
// across endpoints (e.g. `water_risk` live vs. `water_balance` in preview data)
// — both are mapped so this section reads correctly in either mode.
const DISPLAY_LABEL: Record<string, string> = {
  water_risk: 'Water availability',
  water_balance: 'Water availability',
  weather_risk: 'Weather',
  weather_outlook: 'Weather',
  crop_health: 'Crop health',
  soil_suitability: 'Soil conditions',
  disease_risk: 'Disease pressure',
  disease_pressure: 'Disease pressure',
}

const DISPLAY_ICON: Record<string, typeof Droplets> = {
  water_risk: Droplets,
  water_balance: Droplets,
  weather_risk: CloudSun,
  weather_outlook: CloudSun,
  crop_health: Sprout,
  soil_suitability: Leaf,
  disease_risk: Bug,
  disease_pressure: Bug,
}

/** A lightweight acknowledgement of the same factors driving Ask TerraNex's answers — not a re-run of the Farm Health breakdown. */
export function FarmContext({ factors }: { factors: ScoredFactor[] }) {
  if (factors.length === 0) return null

  return (
    <section>
      <p className="mb-2.5 text-xs font-medium tracking-wide text-[color:var(--color-ink-faint)] uppercase">TerraNex is considering</p>
      <div className="flex flex-wrap gap-2">
        {factors.map((factor) => {
          const Icon = DISPLAY_ICON[factor.key] ?? Leaf
          const color = STATUS_COLOR[statusFromScoreBand(factor.band)]
          return (
            <span
              key={factor.key}
              className="flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.02] px-3 py-1.5 text-xs text-[color:var(--color-ink-muted)]"
            >
              <Icon size={12} strokeWidth={1.5} style={{ color }} />
              {DISPLAY_LABEL[factor.key] ?? factor.label}
            </span>
          )
        })}
      </div>
    </section>
  )
}
