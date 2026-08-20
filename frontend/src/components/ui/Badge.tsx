import type { ReactNode } from 'react'
import type { components } from '../../api/types.gen'
import { cn } from '../../lib/cn'
import { STATUS_BG_CLASS, STATUS_TEXT_CLASS, statusFromRiskLevel, statusFromScoreBand } from '../../lib/status'
import type { RiskLevel, ScoreBand } from '../../lib/status'

type DataMode = components['schemas']['DataMode']
type AIMode = components['schemas']['AIMode']

const DATA_MODE_LABEL: Record<DataMode, string> = {
  live: 'Live',
  cached: 'Cached',
  simulated: 'Simulated data',
  unavailable: 'Estimated',
}

const DATA_MODE_CLASS: Record<DataMode, string> = {
  live: 'bg-[color:var(--color-mode-live)]/10 text-[color:var(--color-mode-live)]',
  cached: 'bg-[color:var(--color-mode-cached)]/10 text-[color:var(--color-mode-cached)]',
  simulated: 'bg-[color:var(--color-mode-simulated)]/10 text-[color:var(--color-mode-simulated)]',
  unavailable: 'bg-[color:var(--color-mode-unavailable)]/10 text-[color:var(--color-mode-unavailable)]',
}

const AI_MODE_LABEL: Record<AIMode, string> = {
  gemini: 'AI-generated',
  mock: 'Mock AI',
  fallback: 'AI fallback',
}

const AI_MODE_CLASS: Record<AIMode, string> = {
  gemini: 'bg-lime-500/10 text-lime-300',
  mock: 'bg-white/5 text-[color:var(--color-ink-muted)]',
  fallback: 'bg-[color:var(--color-mode-simulated)]/10 text-[color:var(--color-mode-simulated)]',
}

function BadgeBase({ className, children }: { className: string; children: ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        className,
      )}
    >
      {children}
    </span>
  )
}

/** Renders `meta.mode` per docs/API_CONTRACT.md §4 — always show this next to environmental data. */
export function DataModeBadge({ mode }: { mode: DataMode }) {
  return <BadgeBase className={DATA_MODE_CLASS[mode]}>{DATA_MODE_LABEL[mode]}</BadgeBase>
}

/** Renders `ai_mode` on analyses and image diagnoses. */
export function AIModeBadge({ mode }: { mode: AIMode }) {
  return <BadgeBase className={AI_MODE_CLASS[mode]}>{AI_MODE_LABEL[mode]}</BadgeBase>
}

/** Renders a `ScoreBand` or `RiskLevel` with the shared status color language. */
export function StatusBadge({ label, band, level }: { label: string; band?: ScoreBand; level?: RiskLevel }) {
  const status = band ? statusFromScoreBand(band) : level ? statusFromRiskLevel(level) : 'good'
  return <BadgeBase className={cn(STATUS_BG_CLASS[status], STATUS_TEXT_CLASS[status])}>{label}</BadgeBase>
}
