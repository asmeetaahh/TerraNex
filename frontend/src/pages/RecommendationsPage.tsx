import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { useEnumCatalog } from '../hooks/useEnumCatalog'
import { PreviewBanner } from '../components/dashboard/PreviewBanner'
import { EmptyAnalysisState } from '../components/dashboard/EmptyAnalysisState'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { DashboardErrorState } from '../components/dashboard/DashboardErrorState'
import { AdvisoryOverview } from '../components/dashboard/AdvisoryOverview'
import { PriorityRecommendationCard } from '../components/recommendations/PriorityRecommendationCard'
import { RecommendationCard, type RecommendationItem } from '../components/recommendations/RecommendationCard'

const PRIORITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 }

export function RecommendationsPage() {
  const { mode, farms, selectedFarmId, dashboard, error } = useFarmDashboard()
  const { data: enums } = useEnumCatalog()

  const selectedFarm = farms.find((farm) => farm.id === selectedFarmId) ?? null

  if (mode === 'loading') {
    return <DashboardSkeleton />
  }

  if (mode === 'error') {
    return <DashboardErrorState message={error?.message ?? 'Something went wrong loading recommendations.'} />
  }

  const analysis = dashboard?.analysis
  const advisories = analysis?.advisories ?? []
  const regenerative = analysis?.regenerative_recommendations ?? []

  const sortedAdvisories = [...advisories].sort((a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority])
  const featured = sortedAdvisories[0] ?? null

  const items: RecommendationItem[] = [
    ...sortedAdvisories.filter((a) => a.id !== featured?.id).map((a): RecommendationItem => ({ kind: 'advisory', data: a })),
    ...[...regenerative].sort((a, b) => a.rank - b.rank).map((r): RecommendationItem => ({ kind: 'regenerative', data: r })),
  ]

  return (
    <div className="space-y-6 pb-10">
      {mode === 'preview' && <PreviewBanner />}

      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">Recommendations</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
          TerraNex ranks practical actions for {selectedFarm?.name ?? 'your farm'} based on its current conditions —
          from urgent fixes to longer-term regenerative practices.
        </p>
      </header>

      {!dashboard || !dashboard.has_analysis || !analysis ? (
        <EmptyAnalysisState farmName={selectedFarm?.name ?? 'this farm'} />
      ) : (
        <div className="space-y-6">
          {featured && (
            <section>
              <h2 className="mb-3 text-base font-semibold text-[color:var(--color-ink)]">Priority Recommendation</h2>
              <PriorityRecommendationCard advisory={featured} enums={enums} />
            </section>
          )}

          <section>
            <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">Recommended Actions</h2>
            <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">
              Immediate advisories and longer-term regenerative practices for this farm
            </p>

            {items.length === 0 ? (
              <p className="py-4 text-sm text-[color:var(--color-ink-faint)]">No recommendations for this run.</p>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {items.map((item) => (
                  <RecommendationCard key={item.kind === 'advisory' ? item.data.id : item.data.practice_code} item={item} enums={enums} />
                ))}
              </div>
            )}
          </section>

          <div className="max-w-xl">
            <AdvisoryOverview advisories={advisories} enums={enums} />
          </div>
        </div>
      )}
    </div>
  )
}
