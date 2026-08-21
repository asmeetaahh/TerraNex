import { Database } from 'lucide-react'
import { Card } from '../ui/Card'

export function EmptyMetricState({ message }: { message: string }) {
  return (
    <Card className="flex flex-col items-center justify-center gap-2 p-10 text-center">
      <Database size={22} strokeWidth={1.5} className="text-[color:var(--color-ink-faint)]" />
      <p className="max-w-sm text-sm text-[color:var(--color-ink-faint)]">{message}</p>
    </Card>
  )
}
