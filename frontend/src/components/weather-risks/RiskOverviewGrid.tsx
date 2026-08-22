import { Bug, Droplets, Flame } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import { Card } from '../ui/Card'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_COLOR, statusFromRiskLevel, titleCase } from '../../lib/status'

type WeatherRisk = components['schemas']['WeatherRisk']
type DiseaseRisk = components['schemas']['DiseaseRisk']
type WaterRisk = components['schemas']['WaterRisk']

export function RiskOverviewGrid({
  weatherRisk,
  diseaseRisk,
  waterRisk,
  enums,
}: {
  weatherRisk: WeatherRisk
  diseaseRisk: DiseaseRisk
  waterRisk: WaterRisk
  enums: EnumCatalog | null
}) {
  const risks = [
    {
      key: 'fungal_disease',
      label: 'Fungal Disease',
      icon: Bug,
      level: diseaseRisk.level,
      explanation: diseaseRisk.conditions_summary,
      metric: `${diseaseRisk.risks?.length ?? 0} pathogen${diseaseRisk.risks?.length === 1 ? '' : 's'} tracked`,
    },
    {
      key: 'heat_stress',
      label: 'Heat Stress',
      icon: Flame,
      level: weatherRisk.level,
      explanation: weatherRisk.explanation,
      metric: `${weatherRisk.heat_stress_days} day${weatherRisk.heat_stress_days === 1 ? '' : 's'} forecast above threshold`,
    },
    {
      key: 'water_stress',
      label: 'Water Stress',
      icon: Droplets,
      level: waterRisk.level,
      explanation: waterRisk.explanation,
      metric: `${waterRisk.water_balance_mm >= 0 ? '+' : ''}${Math.round(waterRisk.water_balance_mm)} mm water balance`,
    },
  ]

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {risks.map((risk) => {
        const Icon = risk.icon
        const status = statusFromRiskLevel(risk.level)
        const color = STATUS_COLOR[status]
        const label = findEnumLabel(enums, 'risk_level', risk.level) ?? titleCase(risk.level)

        return (
          <Card key={risk.key} className="flex flex-col gap-2 p-4">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-ink-muted)]">
                <Icon size={13} strokeWidth={1.5} />
                {risk.label}
              </span>
            </div>
            <p className="text-xl font-semibold" style={{ color }}>
              {label}
            </p>
            <p className="line-clamp-2 text-xs text-[color:var(--color-ink-muted)]">{risk.explanation}</p>
            <p className="mt-auto border-t border-white/[0.06] pt-2 text-[11px] text-[color:var(--color-ink-faint)]">
              {risk.metric}
            </p>
          </Card>
        )
      })}
    </div>
  )
}
