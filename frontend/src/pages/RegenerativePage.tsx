import { Sprout } from 'lucide-react'
import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { PreviewBanner } from '../components/dashboard/PreviewBanner'
import { EmptyAnalysisState } from '../components/dashboard/EmptyAnalysisState'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { DashboardErrorState } from '../components/dashboard/DashboardErrorState'
import { RegenerationScoreCard } from '../components/regenerative/RegenerationScoreCard'
import { KeyIndicators } from '../components/regenerative/KeyIndicators'
import { RegenerationRoadmap } from '../components/regenerative/RegenerationRoadmap'
import { RegenerativePracticeCard } from '../components/regenerative/RegenerativePracticeCard'
import { Card } from '../components/ui/Card'
import { regenerativePotentialScore } from '../lib/regenerative'

export function RegenerativePage() {
  const { mode, farms, selectedFarmId, dashboard, error } = useFarmDashboard()

  const selectedFarm = farms.find((farm) => farm.id === selectedFarmId) ?? null

  if (mode === 'loading') {
    return <DashboardSkeleton />
  }

  if (mode === 'error') {
    return <DashboardErrorState message={error?.message ?? 'Something went wrong loading Regenerative Farming.'} />
  }

  const analysis = dashboard?.analysis
  const practices = analysis?.regenerative_recommendations ?? []
  const sortedPractices = [...practices].sort((a, b) => b.relevance_score - a.relevance_score)
  const score = regenerativePotentialScore(practices)

  return (
    <div className="space-y-6 pb-10">
      {mode === 'preview' && <PreviewBanner />}

      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">Regenerative Farming</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
          Build healthier soil, stronger ecosystems, and more resilient farms.
        </p>
      </header>

      {!dashboard || !dashboard.has_analysis || !analysis ? (
        <EmptyAnalysisState farmName={selectedFarm?.name ?? 'this farm'} />
      ) : (
        <div className="space-y-6">
          <RegenerationScoreCard score={score} practiceCount={practices.length} farmName={selectedFarm?.name ?? 'this farm'} />

          <KeyIndicators analysis={analysis} />

          <section>
            <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">Regenerative Practices</h2>
            <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">
              Practices matched to this farm's soil and management, ranked by relevance
            </p>

            {sortedPractices.length === 0 ? (
              <Card className="p-6 text-sm text-[color:var(--color-ink-faint)]">No regenerative practices matched for this run.</Card>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {sortedPractices.map((practice) => (
                  <RegenerativePracticeCard key={practice.practice_code} practice={practice} />
                ))}
              </div>
            )}
          </section>

          <RegenerationRoadmap practices={sortedPractices} />

          <Card className="flex items-start gap-3 p-4">
            <Sprout size={16} strokeWidth={1.5} className="mt-0.5 shrink-0 text-lime-400" />
            <p className="text-xs leading-relaxed text-[color:var(--color-ink-faint)]">
              <span className="font-medium text-[color:var(--color-ink-muted)]">
                TerraNex uses farm conditions to prioritize regenerative practices.
              </span>{' '}
              Verify major changes with local agricultural expertise.
              {mode === 'preview' && ' Everything on this page is typed sample data for visual review, not a real farm observation.'}
            </p>
          </Card>
        </div>
      )}
    </div>
  )
}
