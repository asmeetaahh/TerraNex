import { ShieldAlert } from 'lucide-react'
import { Card } from '../ui/Card'

export function DashboardErrorState({ message }: { message: string }) {
  return (
    <Card className="flex flex-col items-center gap-3 px-6 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-[color:var(--color-status-critical)]/10">
        <ShieldAlert size={22} strokeWidth={1.5} className="text-[color:var(--color-status-critical)]" />
      </span>
      <h2 className="text-base font-semibold text-[color:var(--color-ink)]">Couldn't load the dashboard</h2>
      <p className="max-w-sm text-sm text-[color:var(--color-ink-faint)]">{message}</p>
    </Card>
  )
}
