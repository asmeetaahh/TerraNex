import type { components } from '../api/types.gen'

type VegetationSeries = components['schemas']['VegetationSeries']
type WeatherBundle = components['schemas']['WeatherBundle']
type WaterRisk = components['schemas']['WaterRisk']
type AnalysisRun = components['schemas']['AnalysisRun']

export type MetricId = 'ndvi' | 'water_balance' | 'temperature' | 'rainfall'

export interface MetricPoint {
  date: string
  value: number
}

export interface MetricDefinition {
  id: MetricId
  label: string
  shortLabel: string
  unit: string
  color: string
  /** 'historical' = real past window · 'forecast' = real forward window (backend has no daily weather history) · 'single' = only one current measurement exists. */
  kind: 'historical' | 'forecast' | 'single'
  rangeOptions: number[]
}

export const METRICS: MetricDefinition[] = [
  { id: 'ndvi', label: 'Crop Health / NDVI', shortLabel: 'NDVI', unit: '', color: 'var(--color-lime-400)', kind: 'historical', rangeOptions: [30, 60, 90] },
  {
    id: 'water_balance',
    label: 'Soil Moisture / Water Balance',
    shortLabel: 'Water Balance',
    unit: 'mm',
    color: 'var(--color-sky-300)',
    kind: 'single',
    rangeOptions: [],
  },
  { id: 'temperature', label: 'Temperature', shortLabel: 'Temperature', unit: '°C', color: 'var(--color-amber-300)', kind: 'forecast', rangeOptions: [7, 14] },
  { id: 'rainfall', label: 'Rainfall', shortLabel: 'Rainfall', unit: 'mm', color: 'var(--color-sky-400)', kind: 'forecast', rangeOptions: [7, 14] },
]

export interface MetricSummary {
  currentValue: number | null
  changeText: string | null
  changeDirection: 'up' | 'down' | 'flat' | null
}

function directionFromDelta(delta: number, epsilon = 0.01): 'up' | 'down' | 'flat' {
  if (delta > epsilon) return 'up'
  if (delta < -epsilon) return 'down'
  return 'flat'
}

/** Real historical NDVI series — already fetched, already carries a computed trend from the backend. */
export function ndviPoints(vegetation: VegetationSeries | null): MetricPoint[] {
  if (!vegetation?.series) return []
  return vegetation.series.filter((p) => p.ndvi != null).map((p) => ({ date: p.date, value: p.ndvi as number }))
}

export function ndviSummary(vegetation: VegetationSeries | null): MetricSummary {
  if (!vegetation || vegetation.current_ndvi == null) return { currentValue: null, changeText: null, changeDirection: null }
  const pct = vegetation.trend_pct
  return {
    currentValue: vegetation.current_ndvi,
    changeText: pct != null ? `${pct > 0 ? '+' : ''}${pct.toFixed(1)}% (${vegetation.trend ?? 'trend'})` : (vegetation.trend ?? null),
    changeDirection: pct != null ? directionFromDelta(pct) : null,
  }
}

/** Real forward-looking forecast series — the backend has no daily weather history, only a 30-day aggregate (`history`). */
export function temperaturePoints(weather: WeatherBundle | null): MetricPoint[] {
  if (!weather?.daily) return []
  return weather.daily
    .filter((d) => d.temp_mean_c != null)
    .map((d) => ({ date: d.date, value: d.temp_mean_c as number }))
    .sort((a, b) => a.date.localeCompare(b.date))
}

export function temperatureSummary(weather: WeatherBundle | null): MetricSummary {
  if (!weather?.current) return { currentValue: null, changeText: null, changeDirection: null }
  const points = temperaturePoints(weather)
  const history = weather.history
  if (points.length === 0 || history?.mean_temp_c == null) {
    return { currentValue: weather.current.temperature_c, changeText: null, changeDirection: null }
  }
  const forecastMean = points.reduce((sum, p) => sum + p.value, 0) / points.length
  const delta = forecastMean - history.mean_temp_c
  return {
    currentValue: weather.current.temperature_c,
    changeText: `${delta > 0 ? '+' : ''}${delta.toFixed(1)}°C vs the last ${history.window_days}-day average`,
    changeDirection: directionFromDelta(delta, 0.1),
  }
}

export function rainfallPoints(weather: WeatherBundle | null): MetricPoint[] {
  if (!weather?.daily) return []
  return weather.daily
    .filter((d) => d.precipitation_mm != null)
    .map((d) => ({ date: d.date, value: d.precipitation_mm as number }))
    .sort((a, b) => a.date.localeCompare(b.date))
}

export function rainfallSummary(weather: WeatherBundle | null): MetricSummary {
  const points = rainfallPoints(weather)
  const current = weather?.current?.precipitation_mm ?? points[0]?.value ?? null
  const history = weather?.history
  if (points.length === 0) return { currentValue: current, changeText: null, changeDirection: null }

  const expectedTotal = points.reduce((sum, p) => sum + p.value, 0)
  if (history?.total_precipitation_mm == null) {
    return { currentValue: current, changeText: `${expectedTotal.toFixed(1)} mm expected over the next ${points.length} days`, changeDirection: null }
  }
  const delta = expectedTotal - history.total_precipitation_mm
  return {
    currentValue: current,
    changeText: `${expectedTotal.toFixed(1)} mm expected next ${points.length}d vs ${history.total_precipitation_mm.toFixed(1)} mm over the last ${history.window_days}d`,
    changeDirection: directionFromDelta(delta, 0.5),
  }
}

/** No time series exists for this — only the latest analysis run's snapshot. Presented honestly as a single point, not a fabricated history. */
export function waterBalanceSummary(waterRisk: WaterRisk | null): MetricSummary {
  if (!waterRisk) return { currentValue: null, changeText: null, changeDirection: null }
  return {
    currentValue: waterRisk.water_balance_mm,
    changeText: null,
    changeDirection: null,
  }
}

/** The single real data point behind the water-balance summary, dated to when the analysis actually ran. */
export function waterBalancePoints(analysis: AnalysisRun | null | undefined): MetricPoint[] {
  if (!analysis) return []
  return [{ date: analysis.created_at.slice(0, 10), value: analysis.water_risk.water_balance_mm }]
}
