import { Camera, ChevronRight, ListChecks, RefreshCw, Sprout } from 'lucide-react'
import { Card } from '../ui/Card'

interface Action {
  label: string
  icon: typeof Camera
  href?: string
  soon?: boolean
}

const actions: Action[] = [
  { label: 'Run analysis', icon: RefreshCw, soon: true },
  { label: 'Upload crop photo', icon: Camera, href: '#crop-diagnosis' },
  { label: 'View recommendations', icon: Sprout, href: '#crop-recommendations' },
  { label: 'Review advisories', icon: ListChecks, href: '#advisory-overview' },
]

export function QuickActions() {
  return (
    <Card className="p-5">
      <h2 className="mb-3 text-base font-semibold text-[color:var(--color-ink)]">Today's Quick Actions</h2>
      <div className="space-y-1.5">
        {actions.map((action) => {
          const Icon = action.icon
          const content = (
            <>
              <span className="flex items-center gap-2.5 text-sm font-medium text-[color:var(--color-ink)]">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-lime-500/10 text-lime-400">
                  <Icon size={15} strokeWidth={1.5} />
                </span>
                {action.label}
                {action.soon && (
                  <span className="rounded-full border border-white/[0.08] px-1.5 py-0.5 text-[9px] font-normal tracking-wide text-[color:var(--color-ink-faint)] uppercase">
                    Soon
                  </span>
                )}
              </span>
              <ChevronRight size={15} strokeWidth={1.5} className="shrink-0 text-[color:var(--color-ink-faint)]" />
            </>
          )

          if (action.soon) {
            return (
              <div
                key={action.label}
                title="Coming soon"
                className="flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5"
              >
                {content}
              </div>
            )
          }

          return (
            <a
              key={action.label}
              href={action.href}
              className="flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 transition-colors hover:border-lime-500/30 hover:bg-lime-500/[0.05]"
            >
              {content}
            </a>
          )
        })}
      </div>
    </Card>
  )
}
