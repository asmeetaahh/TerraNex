import { useState } from 'react'
import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { useVegetation } from '../hooks/useVegetation'
import { useWeatherBundle } from '../hooks/useWeatherBundle'
import { PreviewBanner } from '../components/dashboard/PreviewBanner'
import { EmptyAnalysisState } from '../components/dashboard/EmptyAnalysisState'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { DashboardErrorState } from '../components/dashboard/DashboardErrorState'
import { FarmOverviewCards } from '../components/data-explorer/FarmOverviewCards'
import { MetricSelector } from '../components/data-explorer/MetricSelector'
import { RangeSelector } from '../components/data-explorer/RangeSelector'
import { MetricChart } from '../components/data-explorer/MetricChart'
import { MetricSummaryCard } from '../components/data-explorer/MetricSummaryCard'
import { HistoricalTable } from '../components/data-explorer/HistoricalTable'
import { EmptyMetricState } from '../components/data-explorer/EmptyMetricState'
import { Card } from '../components/ui/Card'
import {
  METRICS,
  ndviPoints,
  ndviSummary,
  rainfallPoints,
  rainfallSummary,
  temperaturePoints,
  temperatureSummary,
  waterBalancePoints,
  waterBalanceSummary,
  type MetricId,
  type MetricPoint,
  type MetricSummary,
} from '../lib/dataExplorer'

export function DataExplorerPage() {
  const { mode, farms, selectedFarmId, dashboard, error } = useFarmDashboard()
  const [selectedMetric, setSelectedMetric] = useState<MetricId>('ndvi')
  const [ndviDays, setNdviDays] = useState(90)
  const [forecastDays, setForecastDays] = useState(7)

  const vegetation = useVegetation(selectedFarmId, mode, ndviDays)
  const weather = useWeatherBundle(selectedFarmId, mode, forecastDays)

  const selectedFarm = farms.find((farm) => farm.id === selectedFarmId) ?? null

  if (mode === 'loading') {
    return <DashboardSkeleton />
  }

  if (mode === 'error') {
    return <DashboardErrorState message={error?.message ?? 'Something went wrong loading Data Explorer.'} />
  }

  const analysis = dashboard?.analysis ?? null

  const summaries: Record<MetricId, MetricSummary> = {
    ndvi: ndviSummary(vegetation.data),
    water_balance: waterBalanceSummary(analysis?.water_risk ?? null),
    temperature: temperatureSummary(weather.data),
    rainfall: rainfallSummary(weather.data),
  }

  const metric = METRICS.find((m) => m.id === selectedMetric)!

  let points: MetricPoint[]
  let unavailable = false
  if (selectedMetric === 'ndvi') {
    points = ndviPoints(vegetation.data)
    unavailable = vegetation.unavailable
  } else if (selectedMetric === 'temperature') {
    points = temperaturePoints(weather.data)
    unavailable = weather.unavailable
  } else if (selectedMetric === 'rainfall') {
    points = rainfallPoints(weather.data)
    unavailable = weather.unavailable
  } else {
    points = waterBalancePoints(analysis)
  }

  return (
    <div className="space-y-6 pb-10">
      {mode === 'preview' && <PreviewBanner />}

      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">Data Explorer</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
          Explore {selectedFarm?.name ?? 'your farm'}'s measured conditions over time.
        </p>
      </header>

      {!dashboard || !dashboard.has_analysis || !analysis ? (
        <EmptyAnalysisState farmName={selectedFarm?.name ?? 'this farm'} />
      ) : (
        <div className="space-y-6">
          <FarmOverviewCards summaries={summaries} />

          <Card className="p-4 sm:p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <MetricSelector selected={selectedMetric} onSelect={setSelectedMetric} />
              {metric.kind === 'historical' && <RangeSelector options={metric.rangeOptions} selected={ndviDays} onSelect={setNdviDays} forward={false} />}
              {metric.kind === 'forecast' && (
                <RangeSelector options={metric.rangeOptions} selected={forecastDays} onSelect={setForecastDays} forward />
              )}
            </div>

            <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_220px]">
              <div className="min-w-0">
                {unavailable ? (
                  <EmptyMetricState message={`${metric.label} couldn't be loaded for this farm right now.`} />
                ) : points.length < 2 ? (
                  <EmptyMetricState
                    message={
                      metric.kind === 'single'
                        ? `Only one ${metric.label.toLowerCase()} measurement exists yet — a trend chart needs more than one analysis run over time.`
                        : `Not enough ${metric.label.toLowerCase()} data yet to chart.`
                    }
                  />
                ) : (
                  <Card className="p-4">
                    <MetricChart points={points} color={metric.color} />
                  </Card>
                )}
                {metric.kind === 'forecast' && (
                  <p className="mt-2 text-[11px] text-[color:var(--color-ink-faint)]">
                    TerraNex doesn't store daily weather history yet — this shows the forecast window instead.
                  </p>
                )}
              </div>
              <MetricSummaryCard metric={metric} summary={summaries[selectedMetric]} />
            </div>
          </Card>

          <section>
            <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">Historical Data</h2>
            <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">Every {metric.label.toLowerCase()} data point behind the chart above</p>
            <HistoricalTable metric={metric} points={points} />
          </section>
        </div>
      )}
    </div>
  )
}
