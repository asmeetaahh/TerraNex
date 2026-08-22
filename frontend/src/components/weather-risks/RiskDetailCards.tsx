import { Bug, Droplets, Flame } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { STATUS_TEXT_CLASS, statusFromRiskLevel } from '../../lib/status'

type WeatherRisk = components['schemas']['WeatherRisk']
type DiseaseRisk = components['schemas']['DiseaseRisk']
type WaterRisk = components['schemas']['WaterRisk']

export function RiskDetailCards({
  weatherRisk,
  diseaseRisk,
  waterRisk,
}: {
  weatherRisk: WeatherRisk
  diseaseRisk: DiseaseRisk
  waterRisk: WaterRisk
}) {
  const waterStatus = statusFromRiskLevel(waterRisk.level)
  const weatherStatus = statusFromRiskLevel(weatherRisk.level)
  const diseaseStatus = statusFromRiskLevel(diseaseRisk.level)

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="p-5">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[color:var(--color-ink)]">
          <Droplets size={15} strokeWidth={1.5} className="text-sky-300" />
          Water Stress — why it's flagged
        </h3>
        <p className="mt-2 text-sm text-[color:var(--color-ink-muted)]">{waterRisk.explanation}</p>
        <dl className="mt-3 space-y-1.5 border-t border-white/[0.06] pt-3 text-xs">
          <div className="flex justify-between">
            <dt className="text-[color:var(--color-ink-faint)]">Water balance</dt>
            <dd className="font-medium text-[color:var(--color-ink)]">{Math.round(waterRisk.water_balance_mm)} mm</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-[color:var(--color-ink-faint)]">Deficit</dt>
            <dd className="font-medium text-[color:var(--color-ink)]">{Math.round(waterRisk.deficit_mm)} mm</dd>
          </div>
          {waterRisk.days_until_stress != null && (
            <div className="flex justify-between">
              <dt className="text-[color:var(--color-ink-faint)]">Days until stress</dt>
              <dd className="font-medium text-[color:var(--color-ink)]">{waterRisk.days_until_stress}</dd>
            </div>
          )}
        </dl>
        {waterRisk.drivers && waterRisk.drivers.length > 0 && (
          <ul className="mt-3 space-y-1">
            {waterRisk.drivers.map((driver) => (
              <li key={driver} className="flex items-start gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
                <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_TEXT_CLASS[waterStatus]} bg-current`} />
                {driver}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="p-5">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[color:var(--color-ink)]">
          <Flame size={15} strokeWidth={1.5} className="text-amber-300" />
          Heat Stress — why it's flagged
        </h3>
        <p className="mt-2 text-sm text-[color:var(--color-ink-muted)]">{weatherRisk.explanation}</p>
        <dl className="mt-3 space-y-1.5 border-t border-white/[0.06] pt-3 text-xs">
          <div className="flex justify-between">
            <dt className="text-[color:var(--color-ink-faint)]">Forecast window</dt>
            <dd className="font-medium text-[color:var(--color-ink)]">{weatherRisk.forecast_window_days} days</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-[color:var(--color-ink-faint)]">Heat-stress days</dt>
            <dd className="font-medium text-[color:var(--color-ink)]">{weatherRisk.heat_stress_days}</dd>
          </div>
          {weatherRisk.max_temp_c != null && (
            <div className="flex justify-between">
              <dt className="text-[color:var(--color-ink-faint)]">Peak temperature</dt>
              <dd className="font-medium text-[color:var(--color-ink)]">{weatherRisk.max_temp_c.toFixed(1)}°C</dd>
            </div>
          )}
        </dl>
        {weatherRisk.drivers && weatherRisk.drivers.length > 0 && (
          <ul className="mt-3 space-y-1">
            {weatherRisk.drivers.map((driver) => (
              <li key={driver} className="flex items-start gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
                <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_TEXT_CLASS[weatherStatus]} bg-current`} />
                {driver}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="p-5">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[color:var(--color-ink)]">
          <Bug size={15} strokeWidth={1.5} className="text-[color:var(--color-status-warning)]" />
          Disease Pressure — why it's flagged
        </h3>
        <p className="mt-2 text-sm text-[color:var(--color-ink-muted)]">{diseaseRisk.explanation}</p>

        {diseaseRisk.risks && diseaseRisk.risks.length > 0 ? (
          <div className="mt-3 space-y-3 border-t border-white/[0.06] pt-3">
            {diseaseRisk.risks.map((risk) => (
              <div key={risk.name}>
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-medium text-[color:var(--color-ink)]">{risk.name}</p>
                  <span className={`text-[11px] font-medium ${STATUS_TEXT_CLASS[diseaseStatus]}`}>
                    {Math.round(risk.probability * 100)}% likelihood
                  </span>
                </div>
                {risk.pathogen && <p className="text-[11px] italic text-[color:var(--color-ink-faint)]">{risk.pathogen}</p>}
                {risk.scouting_advice && <p className="mt-0.5 text-xs text-[color:var(--color-ink-muted)]">{risk.scouting_advice}</p>}
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-3 border-t border-white/[0.06] pt-3 text-xs text-[color:var(--color-ink-faint)]">
            No pathogens currently flagged.
          </p>
        )}
      </Card>
    </div>
  )
}
