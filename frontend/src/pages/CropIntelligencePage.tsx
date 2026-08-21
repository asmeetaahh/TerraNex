import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { useEnumCatalog } from '../hooks/useEnumCatalog'
import { useVegetation } from '../hooks/useVegetation'
import { PreviewBanner } from '../components/dashboard/PreviewBanner'
import { EmptyAnalysisState } from '../components/dashboard/EmptyAnalysisState'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { DashboardErrorState } from '../components/dashboard/DashboardErrorState'
import { CropOverviewCard } from '../components/crop-intelligence/CropOverviewCard'
import { CropHealthAnalysis } from '../components/crop-intelligence/CropHealthAnalysis'
import { CropRecommendationsPanel } from '../components/crop-intelligence/CropRecommendationsPanel'
import { CropComparison } from '../components/crop-intelligence/CropComparison'
import { CropInsights } from '../components/crop-intelligence/CropInsights'

export function CropIntelligencePage() {
  const { mode, farms, selectedFarmId, dashboard, error } = useFarmDashboard()
  const { data: enums } = useEnumCatalog()
  const vegetation = useVegetation(selectedFarmId, mode)

  const selectedFarm = farms.find((farm) => farm.id === selectedFarmId) ?? null

  if (mode === 'loading') {
    return <DashboardSkeleton />
  }

  if (mode === 'error') {
    return <DashboardErrorState message={error?.message ?? 'Something went wrong loading crop intelligence.'} />
  }

  const analysis = dashboard?.analysis
  const recommendations = analysis?.crop_recommendations ?? []

  return (
    <div className="space-y-6 pb-10">
      {mode === 'preview' && <PreviewBanner />}

      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">Crop Intelligence</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
          TerraNex analyzes {selectedFarm?.name ?? 'your farm'}'s crop suitability, vigour, and performance to help you
          decide what to plant and how to care for it.
        </p>
      </header>

      {!dashboard || !dashboard.has_analysis || !analysis ? (
        <EmptyAnalysisState farmName={selectedFarm?.name ?? 'this farm'} />
      ) : (
        <div className="space-y-6">
          <CropOverviewCard crops={dashboard.crops ?? []} cropHealth={analysis.crop_health} enums={enums} />

          <CropHealthAnalysis
            cropHealth={analysis.crop_health}
            vegetation={vegetation.data}
            vegetationLoading={vegetation.loading}
            vegetationUnavailable={vegetation.unavailable}
            currentWeather={dashboard.current_weather ?? null}
          />

          <CropRecommendationsPanel recommendations={recommendations} />

          <CropComparison recommendations={recommendations} />

          <CropInsights
            advisories={analysis.advisories ?? []}
            cropHealth={analysis.crop_health}
            topRecommendation={recommendations[0] ?? null}
          />
        </div>
      )}
    </div>
  )
}
