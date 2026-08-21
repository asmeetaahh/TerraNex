import { CloudRain, Droplet, Flame } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'

type WaterRisk = components['schemas']['WaterRisk']
type WeatherRisk = components['schemas']['WeatherRisk']
type DiseaseRisk = components['schemas']['DiseaseRisk']

interface ActionItem {
  key: string
  icon: typeof Droplet
  title: string
  body: string
}

/** Actionable guidance derived entirely from real fields already on the analysis run — nothing new invented. */
export function WeatherActions({
  waterRisk,
  weatherRisk,
  diseaseRisk,
}: {
  waterRisk: WaterRisk
  weatherRisk: WeatherRisk
  diseaseRisk: DiseaseRisk
}) {
  const actions: ActionItem[] = []

  if (waterRisk.deficit_mm > 0) {
    actions.push({
      key: 'irrigation',
      icon: Droplet,
      title: `Irrigate ${Math.round(waterRisk.recommended_irrigation_mm)} mm${waterRisk.irrigation_window ? ` ${waterRisk.irrigation_window}` : ''}`,
      body: waterRisk.irrigation_efficiency_note ?? waterRisk.explanation,
    })
  }

  if (weatherRisk.heat_stress_days > 0) {
    actions.push({
      key: 'heat',
      icon: Flame,
      title: `Prepare for ${weatherRisk.heat_stress_days} day${weatherRisk.heat_stress_days === 1 ? '' : 's'} of heat stress`,
      body: weatherRisk.drivers?.[0] ?? weatherRisk.explanation,
    })
  }

  const pathogenAdvice = diseaseRisk.risks?.find((r) => r.preventive_actions && r.preventive_actions.length > 0)
  if (pathogenAdvice?.preventive_actions?.[0]) {
    actions.push({
      key: 'disease',
      icon: CloudRain,
      title: 'Avoid overhead irrigation while disease conditions are elevated',
      body: pathogenAdvice.preventive_actions[0],
    })
  }

  if (actions.length === 0) return null

  return (
    <Card className="p-5">
      <h2 className="text-base font-semibold text-[color:var(--color-ink)]">Weather-Driven Actions</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">What to do next, based on current conditions</p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {actions.map((action) => {
          const Icon = action.icon
          return (
            <div key={action.key} className="flex gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3.5">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-lime-500/10 text-lime-400">
                <Icon size={13} strokeWidth={1.5} />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium text-[color:var(--color-ink)]">{action.title}</p>
                <p className="mt-0.5 text-xs text-[color:var(--color-ink-muted)]">{action.body}</p>
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
