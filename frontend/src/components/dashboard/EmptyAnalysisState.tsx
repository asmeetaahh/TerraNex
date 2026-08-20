import { Sprout } from 'lucide-react'
import { Card } from '../ui/Card'

/**
 * `has_analysis: false` is the normal state of every newly registered farm
 * (docs/API_CONTRACT.md §3) — rendered as a call to action, not an error.
 */
export function EmptyAnalysisState({ farmName }: { farmName: string }) {
  return (
    <Card className="flex flex-col items-center gap-3 px-6 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-lime-500/10">
        <Sprout size={22} strokeWidth={1.5} className="text-lime-400" />
      </span>
      <h2 className="text-base font-semibold text-[color:var(--color-ink)]">No analysis yet for {farmName}</h2>
      <p className="max-w-sm text-sm text-[color:var(--color-ink-faint)]">
        Run the first analysis to see farm health, risk, and crop recommendations here.
      </p>
      <button
        type="button"
        disabled
        title="Coming soon — POST /farms/{farm_id}/analysis is not implemented yet"
        className="mt-1 rounded-lg border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-sm font-medium text-[color:var(--color-ink-faint)] disabled:cursor-not-allowed"
      >
        Run first analysis
      </button>
    </Card>
  )
}
