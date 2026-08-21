import { Activity, CircleCheck, Minus, Thermometer, TrendingDown, TrendingUp } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { DataModeBadge } from '../ui/Badge'
import { NdviTrendChart } from './NdviTrendChart'
import { titleCase } from '../../lib/status'

type CropHealth = components['schemas']['CropHealth']
type WeatherCurrent = components['schemas']['WeatherCurrent']
type VegetationSeries = components['schemas']['VegetationSeries']

const TREND_ICON: Record<string, typeof TrendingUp> = {
  improving: TrendingUp,
  declining: TrendingDown,
  stable: Minus,
}

export function CropHealthAnalysis({
  cropHealth,
  vegetation,
  vegetationLoading,
  vegetationUnavailable,
  currentWeather,
}: {
  cropHealth: CropHealth
  vegetation: VegetationSeries | null
  vegetationLoading: boolean
  vegetationUnavailable: boolean
  currentWeather: WeatherCurrent | null
}) {
  const trend = vegetation?.trend ?? cropHealth.ndvi_trend ?? null
  const TrendIcon = trend && TREND_ICON[trend] ? TREND_ICON[trend] : Minus
  const ndvi = vegetation?.current_ndvi ?? cropHealth.current_ndvi
  const gddPct =
    cropHealth.gdd_accumulated != null && cropHealth.gdd_required
      ? Math.max(0, Math.min(100, (cropHealth.gdd_accumulated / cropHealth.gdd_required) * 100))
      : null

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">Crop Health Analysis</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">Vegetation, growth progress, and conditions behind the score</p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <div className="mb-1 flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-[color:var(--color-ink)]">
              <Activity size={15} strokeWidth={1.5} className="text-lime-400" />
              Vegetation (NDVI)
            </h3>
            {vegetation && <DataModeBadge mode={vegetation.meta.mode} />}
          </div>

          {ndvi != null && (
            <div className="mb-2 flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums text-[color:var(--color-ink)]">{ndvi.toFixed(2)}</span>
              {trend && (
                <span className="flex items-center gap-1 text-xs font-medium text-lime-400">
                  <TrendIcon size={13} strokeWidth={2} />
                  {titleCase(trend)}
                  {vegetation?.trend_pct != null && ` ${vegetation.trend_pct > 0 ? '+' : ''}${vegetation.trend_pct.toFixed(1)}%`}
                </span>
              )}
            </div>
          )}

          {vegetationLoading ? (
            <div className="h-[120px] animate-pulse rounded-lg bg-white/[0.04]" />
          ) : vegetation?.series && vegetation.series.length > 1 ? (
            <NdviTrendChart series={vegetation.series} />
          ) : vegetationUnavailable ? (
            <p className="py-6 text-center text-xs text-[color:var(--color-ink-faint)]">
              Vegetation trend is temporarily unavailable for this farm.
            </p>
          ) : ndvi != null ? (
            <p className="py-6 text-center text-xs text-[color:var(--color-ink-faint)]">
              Current reading only — trend history not available yet.
            </p>
          ) : (
            <p className="py-6 text-center text-xs text-[color:var(--color-ink-faint)]">No vegetation data yet.</p>
          )}
        </Card>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Card className="p-5">
            <h3 className="mb-3 text-sm font-semibold text-[color:var(--color-ink)]">Growth Progress</h3>
            {gddPct != null ? (
              <>
                <div className="flex items-baseline justify-between">
                  <span className="text-xl font-semibold text-[color:var(--color-ink)]">{Math.round(gddPct)}%</span>
                  <span className="text-xs text-[color:var(--color-ink-faint)]">to maturity</span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-lime-400"
                    style={{ width: `${gddPct}%`, boxShadow: '0 0 8px color-mix(in oklab, var(--color-lime-400) 60%, transparent)' }}
                  />
                </div>
                <p className="mt-2 text-[11px] text-[color:var(--color-ink-faint)]">
                  {Math.round(cropHealth.gdd_accumulated ?? 0)} / {Math.round(cropHealth.gdd_required ?? 0)} GDD accumulated
                </p>
              </>
            ) : (
              <p className="text-xs text-[color:var(--color-ink-faint)]">No growing-degree-day data yet.</p>
            )}
          </Card>

          <Card className="p-5">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[color:var(--color-ink)]">
              <Thermometer size={14} strokeWidth={1.5} className="text-lime-400" />
              Conditions
            </h3>
            {currentWeather ? (
              <dl className="space-y-1.5 text-xs">
                <div className="flex justify-between">
                  <dt className="text-[color:var(--color-ink-muted)]">Temperature</dt>
                  <dd className="font-medium text-[color:var(--color-ink)]">{Math.round(currentWeather.temperature_c)}°C</dd>
                </div>
                {currentWeather.humidity_pct != null && (
                  <div className="flex justify-between">
                    <dt className="text-[color:var(--color-ink-muted)]">Humidity</dt>
                    <dd className="font-medium text-[color:var(--color-ink)]">{currentWeather.humidity_pct}%</dd>
                  </div>
                )}
                <div className="flex justify-between">
                  <dt className="text-[color:var(--color-ink-muted)]">Condition</dt>
                  <dd className="font-medium text-[color:var(--color-ink)]">{currentWeather.condition_label ?? '—'}</dd>
                </div>
              </dl>
            ) : (
              <p className="text-xs text-[color:var(--color-ink-faint)]">No current conditions available.</p>
            )}
          </Card>

          <Card className="p-5 sm:col-span-2">
            <h3 className="mb-3 text-sm font-semibold text-[color:var(--color-ink)]">Stress Indicators</h3>
            {cropHealth.stress_indicators && cropHealth.stress_indicators.length > 0 ? (
              <ul className="space-y-1.5">
                {cropHealth.stress_indicators.map((indicator) => (
                  <li key={indicator} className="flex items-start gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[color:var(--color-status-serious)]" />
                    {indicator}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="flex items-center gap-1.5 text-xs text-lime-400">
                <CircleCheck size={13} strokeWidth={1.5} />
                No stress signals detected
              </p>
            )}
          </Card>
        </div>
      </div>
    </section>
  )
}
