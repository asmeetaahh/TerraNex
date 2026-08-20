import { Info } from 'lucide-react'

/**
 * Shown whenever the dashboard is rendering typed fixture data instead of a
 * live response — today that's every load, since `GET /farms` still returns
 * `501 NOT_IMPLEMENTED` (docs/API_CONTRACT.md). Keeps sample content from
 * ever being mistaken for a real observation.
 */
export function PreviewBanner() {
  return (
    <div className="mb-6 flex items-start gap-3 rounded-xl border border-[color:var(--color-mode-simulated)]/25 bg-[color:var(--color-mode-simulated)]/[0.06] px-4 py-3">
      <Info size={16} strokeWidth={1.5} className="mt-0.5 shrink-0 text-[color:var(--color-mode-simulated)]" />
      <p className="text-sm text-[color:var(--color-ink-muted)]">
        <span className="font-medium text-[color:var(--color-ink)]">Design preview.</span> The backend endpoints
        this dashboard reads are not implemented yet (
        <code className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-xs">NOT_IMPLEMENTED</code>). Everything
        below is typed sample data for visual review, not a real observation.
      </p>
    </div>
  )
}
