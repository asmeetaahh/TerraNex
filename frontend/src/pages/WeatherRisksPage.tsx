import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { useEnumCatalog } from '../hooks/useEnumCatalog'
import { useWeatherBundle } from '../hooks/useWeatherBundle'
import { PreviewBanner } from '../components/dashboard/PreviewBanner'
import { EmptyAnalysisState } from '../components/dashboard/EmptyAnalysisState'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { DashboardErrorState } from '../components/dashboard/DashboardErrorState'
import { AdvisoryOverview } from '../components/dashboard/AdvisoryOverview'
import { CurrentWeatherCard } from '../components/weather-risks/CurrentWeatherCard'
import { ForecastList } from '../components/weather-risks/ForecastList'
import { RiskOverviewGrid } from '../components/weather-risks/RiskOverviewGrid'
import { RiskDetailCards } from '../components/weather-risks/RiskDetailCards'
import { WeatherActions } from '../components/weather-risks/WeatherActions'

export function WeatherRisksPage() {
  const { mode, farms, selectedFarmId, dashboard, error } = useFarmDashboard()
  const { data: enums } = useEnumCatalog()
  const weather = useWeatherBundle(selectedFarmId, mode)

  const selectedFarm = farms.find((farm) => farm.id === selectedFarmId) ?? null

  if (mode === 'loading') {
    return <DashboardSkeleton />
  }

  if (mode === 'error') {
    return <DashboardErrorState message={error?.message ?? 'Something went wrong loading weather & risks.'} />
  }

  const analysis = dashboard?.analysis
  const current = weather.data?.current ?? dashboard?.current_weather ?? null

  return (
    <div className="space-y-6 pb-10">
      {mode === 'preview' && <PreviewBanner />}

      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">Weather & Risks</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
          TerraNex combines current weather and forecast conditions for {selectedFarm?.name ?? 'your farm'} to
          identify agricultural risks early.
        </p>
      </header>

      {!dashboard || !dashboard.has_analysis || !analysis ? (
        <EmptyAnalysisState farmName={selectedFarm?.name ?? 'this farm'} />
      ) : (
        <div className="space-y-6">
          <CurrentWeatherCard current={current} meta={weather.data?.meta ?? null} />

          <section>
            <h2 className="mb-3 text-base font-semibold text-[color:var(--color-ink)]">Forecast</h2>
            {weather.loading ? (
              <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-7">
                {Array.from({ length: 7 }).map((_, i) => (
                  <div key={i} className="h-32 animate-pulse rounded-2xl bg-white/[0.04]" />
                ))}
              </div>
            ) : weather.unavailable ? (
              <p className="text-sm text-[color:var(--color-ink-faint)]">Forecast is temporarily unavailable for this farm.</p>
            ) : (
              <ForecastList days={weather.data?.daily ?? []} />
            )}
          </section>

          <section>
            <h2 className="mb-3 text-base font-semibold text-[color:var(--color-ink)]">Risk Overview</h2>
            <RiskOverviewGrid
              weatherRisk={analysis.weather_risk}
              diseaseRisk={analysis.disease_risk}
              waterRisk={analysis.water_risk}
              enums={enums}
            />
          </section>

          <section>
            <h2 className="mb-3 text-base font-semibold text-[color:var(--color-ink)]">Risk Details</h2>
            <RiskDetailCards weatherRisk={analysis.weather_risk} diseaseRisk={analysis.disease_risk} waterRisk={analysis.water_risk} />
          </section>

          <WeatherActions waterRisk={analysis.water_risk} weatherRisk={analysis.weather_risk} diseaseRisk={analysis.disease_risk} />

          <div className="max-w-xl">
            <AdvisoryOverview advisories={analysis.advisories ?? []} enums={enums} />
          </div>
        </div>
      )}
    </div>
  )
}
