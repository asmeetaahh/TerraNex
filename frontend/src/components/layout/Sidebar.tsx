import {
  ChevronRight,
  CircleCheck,
  FileSearch,
  Grid2x2,
  Microscope,
  Radar,
  Recycle,
  ScanSearch,
  Settings,
  Sparkles,
  TreePine,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import mascot from '../../assets/TerraNex-mascot.png'
import { cn } from '../../lib/cn'

interface NavItem {
  to: string
  label: string
  icon: typeof Grid2x2
  soon?: boolean
}

const navItems: NavItem[] = [
  { to: '/farms', label: 'Dashboard', icon: Grid2x2 },
  { to: '/farms/farm-health', label: 'Farm Health', icon: TreePine },
  { to: '/farms/crop-intelligence', label: 'Crop Intelligence', icon: ScanSearch },
  { to: '/farms/diagnosis', label: 'Diagnosis', icon: Microscope, soon: true },
  { to: '/farms/recommendations', label: 'Recommendations', icon: Sparkles, soon: true },
  { to: '/farms/weather-risks', label: 'Weather & Risks', icon: Radar, soon: true },
  { to: '/farms/ai-advisory', label: 'AI Advisory', icon: CircleCheck, soon: true },
  { to: '/farms/regenerative', label: 'Regenerative', icon: Recycle, soon: true },
  { to: '/farms/data-explorer', label: 'Data Explorer', icon: FileSearch, soon: true },
  { to: '/farms/settings', label: 'Settings', icon: Settings, soon: true },
]

export function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 px-5 pt-7 pb-6">
        <img src={mascot} alt="TerraNex" width={40} height={40} className="object-contain" />
        <div className="leading-tight">
          <p className="text-xl font-semibold tracking-tight">
            <span className="text-[color:var(--color-ink)]">Terra</span>
            <span className="text-lime-400">Nex</span>
          </p>
          <p className="text-[11px] text-[color:var(--color-ink-faint)]">
            Intelligence for
            <br />
            Smarter Farms
          </p>
        </div>
      </div>

      <nav className="flex-1 space-y-0.5 px-3">
        {navItems.map((item) => {
          const Icon = item.icon

          if (item.soon) {
            return (
              <div
                key={item.to}
                title="Coming soon"
                className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-[color:var(--color-ink-muted)] transition-colors hover:bg-white/[0.04] hover:text-[color:var(--color-ink)]"
              >
                <Icon size={16} strokeWidth={1.5} />
                {item.label}
              </div>
            )
          }

          return (
            <NavLink
              key={item.to}
              to={item.to}
              end
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  'flex items-center justify-between rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-lime-500/10 text-lime-300 shadow-[inset_0_0_0_1px_rgba(159,227,92,0.18),0_0_20px_-8px_rgba(143,224,60,0.55)]'
                    : 'text-[color:var(--color-ink-muted)] hover:bg-white/[0.04] hover:text-[color:var(--color-ink)]',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span className="flex items-center gap-2.5">
                    <Icon size={16} strokeWidth={1.5} />
                    {item.label}
                  </span>
                  {isActive && <ChevronRight size={14} strokeWidth={2} />}
                </>
              )}
            </NavLink>
          )
        })}
      </nav>

      <div className="relative mx-3 mt-4 mb-5 overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
        <p className="text-sm font-medium text-[color:var(--color-ink)]">
          Your farm is <span className="text-lime-400">growing</span>
        </p>
        <p className="mt-1 text-xs leading-relaxed text-[color:var(--color-ink-faint)]">
          Keep going! Your decisions are making a difference.
        </p>
        <img
          src={mascot}
          alt="TerraNex bonsai"
          className="mx-auto mt-2 h-28 w-28 object-contain"
          style={{ filter: 'drop-shadow(0 0 18px color-mix(in oklab, var(--color-lime-400) 45%, transparent))' }}
        />
      </div>
    </div>
  )
}

export function Sidebar() {
  return (
    <aside className="hidden w-64 shrink-0 border-r border-white/[0.06] bg-[color:var(--color-surface)]/60 lg:block">
      <div className="sticky top-0 h-screen overflow-y-auto">
        <SidebarContent />
      </div>
    </aside>
  )
}
