import { Sprout } from 'lucide-react'
import { Card } from '../ui/Card'

const BANDS = [
  { min: 75, color: 'var(--color-status-good)', label: 'Strong potential' },
  { min: 50, color: 'var(--color-status-warning)', label: 'Building potential' },
  { min: 0, color: 'var(--color-status-serious)', label: 'Early stage' },
]

function bandFor(score: number) {
  return BANDS.find((band) => score >= band.min) ?? BANDS[BANDS.length - 1]
}

/**
 * Hero score card — same ring visual language as Farm Health's Overall Health
 * card, but for a derived, presentation-only "regenerative potential" number
 * (see `lib/regenerative.ts`), not a raw backend field.
 */
export function RegenerationScoreCard({
  score,
  practiceCount,
  farmName,
}: {
  score: number | null
  practiceCount: number
  farmName: string
}) {
  if (score === null) {
    return (
      <Card glow className="p-6 sm:p-7">
        <p className="text-sm text-[color:var(--color-ink-faint)]">
          TerraNex doesn't have regenerative practice data for {farmName} yet.
        </p>
      </Card>
    )
  }

  const band = bandFor(score)
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
              stroke={band.color}
              strokeWidth="9"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={circumference * (1 - score / 100)}
              style={{ filter: `drop-shadow(0 0 10px color-mix(in oklab, ${band.color} 65%, transparent))` }}
              className="transition-[stroke-dashoffset] duration-700 ease-out"
            />
          </svg>
          <span className="flex h-16 w-16 items-center justify-center rounded-full bg-lime-500/10 text-lime-400">
            <Sprout size={30} strokeWidth={1.5} />
          </span>
        </div>

        <div className="min-w-0 flex-1 text-center sm:text-left">
          <p className="text-xs font-medium tracking-wide text-[color:var(--color-ink-muted)] uppercase">Farm Regeneration Score</p>
          <p className="mt-1.5 flex items-baseline justify-center gap-1.5 sm:justify-start">
            <span className="text-5xl font-semibold tabular-nums text-[color:var(--color-ink)]">{score}</span>
            <span className="text-base text-[color:var(--color-ink-faint)]">/100</span>
            <span className="ml-1 text-lg font-semibold" style={{ color: band.color }}>
              {band.label}
            </span>
          </p>
          <p className="mx-auto mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--color-ink-muted)] sm:mx-0">
            Based on how strongly TerraNex's {practiceCount} matched regenerative {practiceCount === 1 ? 'practice' : 'practices'} apply
            to {farmName}'s current soil and management conditions — the higher the score, the more long-term upside is available from
            adopting them.
          </p>
        </div>
      </div>
    </Card>
  )
}
