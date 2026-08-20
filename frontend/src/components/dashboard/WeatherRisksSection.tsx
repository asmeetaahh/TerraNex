import { CloudSun } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import { Card } from '../ui/Card'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_TEXT_CLASS, statusFromRiskLevel, titleCase, type RiskLevel } from '../../lib/status'

type WeatherCurrent = components['schemas']['WeatherCurrent']
type WeatherRisk = components['schemas']['WeatherRisk']
type DiseaseRisk = components['schemas']['DiseaseRisk']
type WaterRisk = components['schemas']['WaterRisk']

function heatStressLevel(days: number): RiskLevel {
  if (days >= 4) return 'severe'
  if (days >= 2) return 'high'
  if (days >= 1) return 'moderate'
  return 'low'
}

export function WeatherRisksSection({
  current,
  weatherRisk,
  diseaseRisk,
  waterRisk,
  enums,
}: {
  current: WeatherCurrent | null
  weatherRisk: WeatherRisk
  diseaseRisk: DiseaseRisk
  waterRisk: WaterRisk
  enums: EnumCatalog | null
}) {
  const heatLevel = heatStressLevel(weatherRisk.heat_stress_days)

  const risks: { label: string; level: RiskLevel }[] = [
    { label: 'Fungal Disease', level: diseaseRisk.level },
    { label: 'Heat Stress', level: heatLevel },
    { label: 'Water Stress', level: waterRisk.level },
  ]

  return (
    <Card className="p-5" id="weather-risks">
      <h2 className="text-base font-semibold text-[color:var(--color-ink)]">Weather & Risks</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">Stay ahead with real-time weather and risk alerts</p>

      <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
        <p className="mb-2 text-xs font-medium text-[color:var(--color-ink-muted)]">Current Weather</p>
        {current ? (
          <>
            <div className="flex items-center gap-2">
              <CloudSun size={22} strokeWidth={1.5} className="text-[color:var(--color-ink-muted)]" />
              <div>
                <p className="text-xl font-semibold text-[color:var(--color-ink)]">{Math.round(current.temperature_c)}°C</p>
                <p className="text-[11px] text-[color:var(--color-ink-faint)]">{current.condition_label ?? 'Unknown'}</p>
              </div>
            </div>
            <dl className="mt-2.5 space-y-1 text-xs text-[color:var(--color-ink-muted)]">
              {current.humidity_pct != null && (
                <div className="flex justify-between">
                  <dt>Humidity</dt>
                  <dd className="font-medium text-[color:var(--color-ink)]">{current.humidity_pct}%</dd>
                </div>
              )}
              {current.precipitation_mm != null && (
                <div className="flex justify-between">
                  <dt>Rainfall</dt>
                  <dd className="font-medium text-[color:var(--color-ink)]">{current.precipitation_mm} mm</dd>
                </div>
              )}
              {current.wind_speed_kmh != null && (
                <div className="flex justify-between">
                  <dt>Wind</dt>
                  <dd className="font-medium text-[color:var(--color-ink)]">{Math.round(current.wind_speed_kmh)} km/h</dd>
                </div>
              )}
            </dl>
          </>
        ) : (
          <p className="text-sm text-[color:var(--color-ink-faint)]">No current conditions available.</p>
        )}
      </div>

      <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
        <p className="mb-2 text-xs font-medium text-[color:var(--color-ink-muted)]">Risk Overview</p>
        <div className="space-y-2">
          {risks.map((risk) => {
            const status = statusFromRiskLevel(risk.level)
            const label = findEnumLabel(enums, 'risk_level', risk.level) ?? titleCase(risk.level)
            return (
              <div key={risk.label} className="flex items-center justify-between text-xs">
                <span className="text-[color:var(--color-ink-muted)]">{risk.label}</span>
                <span className={`flex items-center gap-1.5 font-medium ${STATUS_TEXT_CLASS[status]}`}>
                  <span className="h-1.5 w-1.5 rounded-full bg-current" />
                  {label}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </Card>
  )
}
