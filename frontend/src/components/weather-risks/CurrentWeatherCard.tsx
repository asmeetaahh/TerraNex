import { Droplet, Droplets, Gauge, Thermometer, Wind } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { DataModeBadge } from '../ui/Badge'
import { CONDITION_ICONS, conditionIconKey } from './conditionIcon'

type WeatherCurrent = components['schemas']['WeatherCurrent']
type DataSourceMeta = components['schemas']['DataSourceMeta']

export function CurrentWeatherCard({ current, meta }: { current: WeatherCurrent | null; meta: DataSourceMeta | null }) {
  if (!current) {
    return (
      <Card glow className="p-6 sm:p-7">
        <p className="text-sm text-[color:var(--color-ink-faint)]">No current weather available for this farm.</p>
      </Card>
    )
  }

  const Icon = CONDITION_ICONS[conditionIconKey(current.condition)]

  return (
    <Card glow className="p-6 sm:p-7">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-lime-500/10">
            <Icon size={32} strokeWidth={1.5} className="text-lime-400" />
          </span>
          <div>
            <p className="text-xs font-medium tracking-wide text-[color:var(--color-ink-muted)] uppercase">Current Weather</p>
            <p className="mt-1 flex items-baseline gap-2">
              <span className="text-4xl font-semibold tabular-nums text-[color:var(--color-ink)]">
                {Math.round(current.temperature_c)}°C
              </span>
              {current.feels_like_c != null && (
                <span className="text-sm text-[color:var(--color-ink-faint)]">Feels like {Math.round(current.feels_like_c)}°C</span>
              )}
            </p>
            <p className="text-sm text-[color:var(--color-ink-muted)]">{current.condition_label ?? 'Conditions unknown'}</p>
          </div>
        </div>
        {meta && <DataModeBadge mode={meta.mode} />}
      </div>

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {current.humidity_pct != null && (
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
            <Droplet size={14} strokeWidth={1.5} className="text-sky-300" />
            <p className="mt-1.5 text-lg font-semibold text-[color:var(--color-ink)]">{current.humidity_pct}%</p>
            <p className="text-[11px] text-[color:var(--color-ink-faint)]">Humidity</p>
          </div>
        )}
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
          <Droplets size={14} strokeWidth={1.5} className="text-sky-300" />
          <p className="mt-1.5 text-lg font-semibold text-[color:var(--color-ink)]">{current.precipitation_mm ?? 0} mm</p>
          <p className="text-[11px] text-[color:var(--color-ink-faint)]">Rainfall</p>
        </div>
        {current.wind_speed_kmh != null && (
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
            <Wind size={14} strokeWidth={1.5} className="text-lime-400" />
            <p className="mt-1.5 text-lg font-semibold text-[color:var(--color-ink)]">{Math.round(current.wind_speed_kmh)} km/h</p>
            <p className="text-[11px] text-[color:var(--color-ink-faint)]">Wind</p>
          </div>
        )}
        {current.pressure_hpa != null && (
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
            <Gauge size={14} strokeWidth={1.5} className="text-amber-300" />
            <p className="mt-1.5 text-lg font-semibold text-[color:var(--color-ink)]">{Math.round(current.pressure_hpa)} hPa</p>
            <p className="text-[11px] text-[color:var(--color-ink-faint)]">Pressure</p>
          </div>
        )}
        {current.cloud_cover_pct != null && (
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
            <Thermometer size={14} strokeWidth={1.5} className="text-[color:var(--color-ink-faint)]" />
            <p className="mt-1.5 text-lg font-semibold text-[color:var(--color-ink)]">{current.cloud_cover_pct}%</p>
            <p className="text-[11px] text-[color:var(--color-ink-faint)]">Cloud cover</p>
          </div>
        )}
      </div>
    </Card>
  )
}
