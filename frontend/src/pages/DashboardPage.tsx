import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { useEnumCatalog } from '../hooks/useEnumCatalog'
import { PreviewBanner } from '../components/dashboard/PreviewBanner'
import { DashboardHeader } from '../components/dashboard/DashboardHeader'
import { HealthCardsGrid } from '../components/dashboard/HealthCardsGrid'
import { AdvisoryPanel } from '../components/dashboard/AdvisoryPanel'
import { QuickActions } from '../components/dashboard/QuickActions'
import { AtAGlanceStrip } from '../components/dashboard/AtAGlanceStrip'
import { CropRecommendations } from '../components/dashboard/CropRecommendations'
import { WeatherRisksSection } from '../components/dashboard/WeatherRisksSection'
import { AdvisoryOverview } from '../components/dashboard/AdvisoryOverview'
import { TreeGrowthSection } from '../components/dashboard/TreeGrowthSection'
import { EmptyAnalysisState } from '../components/dashboard/EmptyAnalysisState'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { DashboardErrorState } from '../components/dashboard/DashboardErrorState'

export function DashboardPage() {
  const { mode, farms, selectedFarmId, dashboard, error, selectFarm } = useFarmDashboard()
  const { data: enums } = useEnumCatalog()

  const selectedFarm = farms.find((farm) => farm.id === selectedFarmId) ?? null

  if (mode === 'loading') {
    return <DashboardSkeleton />
  }

  if (mode === 'error') {
    return <DashboardErrorState message={error?.message ?? 'Something went wrong loading your farms.'} />
  }

  const analysis = dashboard?.analysis

  return (
    <div className="space-y-6 pb-10">
      {mode === 'preview' && <PreviewBanner />}

      <DashboardHeader farms={farms} selectedFarm={selectedFarm} onSelectFarm={selectFarm} />

      {!dashboard || !dashboard.has_analysis || !analysis ? (
        <EmptyAnalysisState farmName={selectedFarm?.name ?? 'this farm'} />
      ) : (
        <div className="space-y-6">
          <HealthCardsGrid analysis={analysis} enums={enums} />

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <AdvisoryPanel
                advisories={analysis.advisories ?? []}
                factors={analysis.factors ?? []}
                aiMode={analysis.ai_mode}
              />
            </div>
            <QuickActions />
          </div>

          <AtAGlanceStrip current={dashboard.current_weather ?? null} soilMoisturePct={analysis.water_risk.soil_moisture_pct} />

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
            <CropRecommendations recommendations={analysis.crop_recommendations ?? []} />
            <WeatherRisksSection
              current={dashboard.current_weather ?? null}
              weatherRisk={analysis.weather_risk}
              diseaseRisk={analysis.disease_risk}
              waterRisk={analysis.water_risk}
              enums={enums}
            />
            <AdvisoryOverview advisories={analysis.advisories ?? []} enums={enums} />
          </div>

          <TreeGrowthSection band={analysis.overall_band} />
        </div>
      )}
    </div>
  )
}
