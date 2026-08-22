import { ShieldAlert } from 'lucide-react'
import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { PreviewBanner } from '../components/dashboard/PreviewBanner'
import { EmptyAnalysisState } from '../components/dashboard/EmptyAnalysisState'
import { DashboardSkeleton } from '../components/dashboard/DashboardSkeleton'
import { DashboardErrorState } from '../components/dashboard/DashboardErrorState'
import { AskTerraNex } from '../components/ai-advisory/AskTerraNex'
import { FarmContext } from '../components/ai-advisory/FarmContext'
import { Card } from '../components/ui/Card'

export function AiAdvisoryPage() {
  const { mode, farms, selectedFarmId, dashboard, error } = useFarmDashboard()

  const selectedFarm = farms.find((farm) => farm.id === selectedFarmId) ?? null

  if (mode === 'loading') {
    return <DashboardSkeleton />
  }

  if (mode === 'error') {
    return <DashboardErrorState message={error?.message ?? 'Something went wrong loading AI Advisory.'} />
  }

  const analysis = dashboard?.analysis
  const advisories = analysis?.advisories ?? []
  const factors = analysis?.factors ?? []
  const regenerative = analysis?.regenerative_recommendations ?? []

  return (
    <div className="space-y-6 pb-10">
      {mode === 'preview' && <PreviewBanner />}

      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">AI Advisory</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
          Talk to TerraNex about {selectedFarm?.name ?? 'your farm'}'s crops, weather, and risks.
        </p>
      </header>

      {!dashboard || !dashboard.has_analysis || !analysis ? (
        <EmptyAnalysisState farmName={selectedFarm?.name ?? 'this farm'} />
      ) : (
        <div className="space-y-6">
          <AskTerraNex data={{ advisories, factors, regenerative }} aiMode={analysis.ai_mode} />

          <FarmContext factors={factors} />

          <Card className="flex items-start gap-3 p-4">
            <ShieldAlert size={16} strokeWidth={1.5} className="mt-0.5 shrink-0 text-[color:var(--color-status-warning)]" />
            <p className="text-xs leading-relaxed text-[color:var(--color-ink-faint)]">
              <span className="font-medium text-[color:var(--color-ink-muted)]">AI-assisted decision support</span> — verify
              high-impact agricultural decisions with an agronomist or local expert.
              {mode === 'preview' && ' Everything on this page is typed sample data for visual review, not a real farm observation.'}
            </p>
          </Card>
        </div>
      )}
    </div>
  )
}
