import { Menu, X } from 'lucide-react'
import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import mascot from '../../assets/TerraNex-mascot.png'
import { Sidebar, SidebarContent } from './Sidebar'

export function DashboardLayout() {
  const [drawerOpen, setDrawerOpen] = useState(false)

  return (
    <div className="flex min-h-screen bg-[color:var(--color-canvas)]">
      <Sidebar />

      {/* Mobile top bar */}
      <div className="fixed inset-x-0 top-0 z-30 flex items-center justify-between border-b border-white/[0.06] bg-[color:var(--color-canvas)]/90 px-4 py-3 backdrop-blur-sm lg:hidden">
        <div className="flex items-center gap-2">
          <img src={mascot} alt="TerraNex" width={26} height={26} className="object-contain" />
          <span className="text-sm font-semibold">
            <span className="text-[color:var(--color-ink)]">Terra</span>
            <span className="text-lime-400">Nex</span>
          </span>
        </div>
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open navigation"
          className="rounded-md p-1.5 text-[color:var(--color-ink-muted)] hover:bg-white/[0.06]"
        >
          <Menu size={20} strokeWidth={1.5} />
        </button>
      </div>

      {/* Mobile drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            className="absolute inset-0 bg-black/60"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-72 border-r border-white/[0.06] bg-[color:var(--color-surface)]">
            <button
              type="button"
              onClick={() => setDrawerOpen(false)}
              aria-label="Close navigation"
              className="absolute top-6 right-3 rounded-md p-1.5 text-[color:var(--color-ink-muted)] hover:bg-white/[0.06]"
            >
              <X size={18} strokeWidth={1.5} />
            </button>
            <SidebarContent onNavigate={() => setDrawerOpen(false)} />
          </div>
        </div>
      )}

      <main className="min-w-0 flex-1 px-4 pt-20 pb-12 sm:px-6 lg:px-10 lg:pt-10">
        <div className="mx-auto max-w-7xl">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
