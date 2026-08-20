import { ArrowUpRight, Dot, Sparkles } from 'lucide-react'
import type { components } from '../../api/types.gen'
import mascot from '../../assets/TerraNex-mascot.png'
import { Card } from '../ui/Card'
import { AIModeBadge } from '../ui/Badge'

type Advisory = components['schemas']['Advisory']
type AIMode = components['schemas']['AIMode']
type ScoredFactor = components['schemas']['ScoredFactor']

const PRIORITY_ORDER: Record<Advisory['priority'], number> = { critical: 0, high: 1, medium: 2, low: 3 }

export function AdvisoryPanel({
  advisories,
  factors,
  aiMode,
}: {
  advisories: Advisory[]
  factors: ScoredFactor[]
  aiMode: AIMode
}) {
  const featured = [...advisories].sort((a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority])[0]
  const whyTags = factors.slice(0, 2)

  return (
    <Card className="grid gap-6 p-5 md:grid-cols-2">
      <div className="flex min-w-0 flex-col">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-[color:var(--color-ink)]">
            <Sparkles size={16} strokeWidth={1.5} className="text-lime-400" />
            AI Advisory
          </h2>
          <AIModeBadge mode={aiMode} />
        </div>

        {!featured ? (
          <p className="text-sm text-[color:var(--color-ink-faint)]">No advisories for this run.</p>
        ) : (
          <>
            <p className="text-sm leading-relaxed text-[color:var(--color-ink-muted)]">{featured.body}</p>

            {whyTags.length > 0 && (
              <div className="mt-4">
                <p className="text-xs font-medium text-[color:var(--color-ink-faint)]">Why this?</p>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {whyTags.map((factor) => (
                    <span
                      key={factor.key}
                      className="flex items-center gap-0.5 rounded-full border border-white/[0.08] bg-white/[0.02] py-1 pr-2.5 pl-1 text-xs text-[color:var(--color-ink-muted)]"
                    >
                      <Dot size={18} strokeWidth={4} className="text-lime-400" />
                      {factor.label}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-4">
              <div className="flex items-center justify-between text-xs">
                <span className="text-[color:var(--color-ink-faint)]">Confidence</span>
                <span className="font-medium text-lime-400">{Math.round(featured.confidence * 100)}%</span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                <div
                  className="h-full rounded-full bg-lime-400"
                  style={{
                    width: `${Math.round(featured.confidence * 100)}%`,
                    boxShadow: '0 0 8px color-mix(in oklab, var(--color-lime-400) 70%, transparent)',
                  }}
                />
              </div>
            </div>
          </>
        )}
      </div>

      <div className="relative flex min-h-[180px] items-center justify-center overflow-hidden rounded-xl border border-white/[0.06]">
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(ellipse at 50% 35%, color-mix(in oklab, var(--color-lime-500) 16%, transparent), transparent 70%), var(--color-surface)',
          }}
        />
        <img
          src={mascot}
          alt="TerraNex"
          className="relative h-32 w-32 object-contain"
          style={{ filter: 'drop-shadow(0 0 24px color-mix(in oklab, var(--color-lime-400) 55%, transparent))' }}
        />
        <a
          href="#advisory-overview"
          className="absolute right-3 bottom-3 flex items-center gap-1 rounded-full border border-lime-500/30 bg-[color:var(--color-canvas)]/70 px-3 py-1.5 text-xs font-medium text-lime-300 backdrop-blur-sm transition-colors hover:bg-lime-500/10"
        >
          View all advisories
          <ArrowUpRight size={13} strokeWidth={2} />
        </a>
      </div>
    </Card>
  )
}
