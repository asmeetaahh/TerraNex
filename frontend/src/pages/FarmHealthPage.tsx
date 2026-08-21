import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { useEnumCatalog } from '../hooks/useEnumCatalog'
import { PreviewBanner } from '../components/dashboard/PreviewBanner'
import { EmptyAnalysisState } from '../components/dashboard/EmptyAnalysisState'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { DashboardErrorState } from '../components/dashboard/DashboardErrorState'
import { OverallHealthCard } from '../components/farm-health/OverallHealthCard'
import { HealthFactorCards } from '../components/farm-health/HealthFactorCards'
import { HealthBreakdown } from '../components/farm-health/HealthBreakdown'
import { KeyConcerns } from '../components/farm-health/KeyConcerns'
import { ImprovementRecommendations } from '../components/farm-health/ImprovementRecommendations'

export function FarmHealthPage() {
  const { mode, farms, selectedFarmId, dashboard, error } = useFarmDashboard()
  const { data: enums } = useEnumCatalog()

  const selectedFarm = farms.find((farm) => farm.id === selectedFarmId) ?? null

  if (mode === 'loading') {
    return <DashboardSkeleton />
  }

  if (mode === 'error') {
    return <DashboardErrorState message={error?.message ?? 'Something went wrong loading farm health.'} />
  }

  const analysis = dashboard?.analysis

  return (
    <div className="space-y-6 pb-10">
      {mode === 'preview' && <PreviewBanner />}

      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">Farm Health</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
          A deeper view of {selectedFarm?.name ?? 'your farm'}'s overall condition — how each factor contributes to
          the Farm Health Score, and what to do about it.
        </p>
      </header>

      {!dashboard || !dashboard.has_analysis || !analysis ? (
        <EmptyAnalysisState farmName={selectedFarm?.name ?? 'this farm'} />
      ) : (
        <div className="space-y-6">
          <OverallHealthCard analysis={analysis} enums={enums} />

          <HealthFactorCards analysis={analysis} enums={enums} />

          <HealthBreakdown factors={analysis.factors ?? []} />

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <KeyConcerns advisories={analysis.advisories ?? []} enums={enums} />
            <ImprovementRecommendations recommendations={analysis.regenerative_recommendations ?? []} />
          </div>
        </div>
      )}
    </div>
  )
}
