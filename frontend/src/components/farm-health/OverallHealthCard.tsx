import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import mascot from '../../assets/TerraNex-mascot.png'
import { Card } from '../../components/ui/Card'
import { AIModeBadge } from '../../components/ui/Badge'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_COLOR, statusFromScoreBand, titleCase } from '../../lib/status'

type AnalysisRun = components['schemas']['AnalysisRun']

/** The hero score card — same ring + mascot visual language as the Dashboard's Farm Health Score card, sized up for its own page. */
export function OverallHealthCard({ analysis, enums }: { analysis: AnalysisRun; enums: EnumCatalog | null }) {
  const status = statusFromScoreBand(analysis.overall_band)
  const bandLabel = findEnumLabel(enums, 'score_band', analysis.overall_band) ?? titleCase(analysis.overall_band)
  const color = STATUS_COLOR[status]
  const score = analysis.overall_health_score
  const circumference = 2 * Math.PI * 54

  return (
    <Card glow className="p-6 sm:p-7">
      <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center">
        <div className="relative flex h-40 w-40 shrink-0 items-center justify-center">
          <svg viewBox="0 0 128 128" className="absolute inset-0 -rotate-90">
            <circle cx="64" cy="64" r="54" fill="none" stroke="var(--color-border)" strokeWidth="9" />
            <circle
              cx="64"
              cy="64"
              r="54"
              fill="none"
              stroke={color}
              strokeWidth="9"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={circumference * (1 - score / 100)}
              style={{ filter: `drop-shadow(0 0 10px color-mix(in oklab, ${color} 65%, transparent))` }}
              className="transition-[stroke-dashoffset] duration-700 ease-out"
            />
          </svg>
          <img src={mascot} alt="" aria-hidden="true" className="h-20 w-20 object-contain" />
        </div>

        <div className="min-w-0 flex-1 text-center sm:text-left">
          <div className="flex flex-wrap items-center justify-center gap-2 sm:justify-start">
            <p className="text-xs font-medium tracking-wide text-[color:var(--color-ink-muted)] uppercase">
              Overall Farm Health
            </p>
            <AIModeBadge mode={analysis.ai_mode} />
          </div>
          <p className="mt-1.5 flex items-baseline justify-center gap-1.5 sm:justify-start">
            <span className="text-5xl font-semibold tabular-nums text-[color:var(--color-ink)]">{Math.round(score)}</span>
            <span className="text-base text-[color:var(--color-ink-faint)]">/100</span>
            <span className="ml-1 text-lg font-semibold" style={{ color }}>
              {bandLabel}
            </span>
          </p>
          <p className="mx-auto mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--color-ink-muted)] sm:mx-0">
            {analysis.summary}
          </p>
        </div>
      </div>
    </Card>
  )
}
