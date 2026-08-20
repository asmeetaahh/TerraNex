import { CircleDot, Sprout, TreeDeciduous, TreePine, Trees } from 'lucide-react'
import type { components } from '../../api/types.gen'
import mascot from '../../assets/TerraNex-mascot.png'
import { Card } from '../ui/Card'
import { cn } from '../../lib/cn'

type ScoreBand = components['schemas']['ScoreBand']

const STAGES: { band: ScoreBand; label: string; icon: typeof Sprout }[] = [
  { band: 'critical', label: 'Seed', icon: CircleDot },
  { band: 'poor', label: 'Sprout', icon: Sprout },
  { band: 'moderate', label: 'Sapling', icon: TreeDeciduous },
  { band: 'good', label: 'Growing Tree', icon: TreePine },
  { band: 'excellent', label: 'Thriving Tree', icon: Trees },
]

const STAGE_MESSAGE: Record<ScoreBand, string> = {
  critical: 'Every farm starts somewhere. Address the biggest risks to start growing.',
  poor: 'First signs of life — keep tending to the basics and momentum will build.',
  moderate: 'Your farm is taking root. Steady, deliberate progress.',
  good: 'Your farm is growing strong. Keep going — your decisions are working.',
  excellent: 'Your farm is thriving. This is what consistent good decisions look like.',
}

/**
 * Presentation-only growth metaphor mapped from the farm's real, backend-computed
 * `overall_band` (docs/API_CONTRACT.md — ScoreBand). No growth-stage field exists
 * in the API, so nothing is invented here: the same five bands already used for
 * the Farm Health Score card simply double as the five stages of this visual.
 */
export function TreeGrowthSection({ band }: { band: ScoreBand }) {
  const currentIndex = STAGES.findIndex((stage) => stage.band === band)
  const current = STAGES[currentIndex]

  return (
    <Card glow className="p-6 sm:p-7">
      <div className="grid grid-cols-1 items-center gap-6 sm:grid-cols-[auto_1fr]">
        <img
          src={mascot}
          alt="TerraNex bonsai"
          className="mx-auto h-24 w-24 shrink-0 object-contain sm:h-28 sm:w-28"
          style={{ filter: 'drop-shadow(0 0 20px color-mix(in oklab, var(--color-lime-400) 50%, transparent))' }}
        />

        <div className="min-w-0">
          <h2 className="text-xl font-semibold text-[color:var(--color-ink)] sm:text-2xl">
            Your farm is <span className="text-lime-400">growing</span>
          </h2>
          <p className="mt-1.5 text-sm text-[color:var(--color-ink-muted)]">{STAGE_MESSAGE[band]}</p>

          <div className="mt-6 flex items-center">
            {STAGES.map((stage, index) => {
              const Icon = stage.icon
              const isCurrent = index === currentIndex
              const isPast = index < currentIndex

              return (
                <div key={stage.band} className="flex flex-1 items-center last:flex-none">
                  <span
                    title={stage.label}
                    className={cn(
                      'flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-colors',
                      isCurrent
                        ? 'border-lime-400 bg-lime-500/15 text-lime-300'
                        : isPast
                          ? 'border-lime-500/30 bg-lime-500/5 text-lime-400/70'
                          : 'border-white/10 bg-white/[0.02] text-[color:var(--color-ink-faint)]',
                    )}
                    style={
                      isCurrent
                        ? { boxShadow: '0 0 14px color-mix(in oklab, var(--color-lime-400) 55%, transparent)' }
                        : undefined
                    }
                  >
                    <Icon size={16} strokeWidth={1.5} />
                  </span>
                  {index < STAGES.length - 1 && (
                    <span className={cn('mx-1.5 h-px flex-1 sm:mx-2', isPast ? 'bg-lime-500/40' : 'bg-white/10')} />
                  )}
                </div>
              )
            })}
          </div>

          <p className="mt-2.5 text-xs text-[color:var(--color-ink-faint)]">
            Stage {currentIndex + 1} of {STAGES.length} — <span className="font-medium text-lime-300">{current.label}</span>
          </p>
        </div>
      </div>
    </Card>
  )
}
