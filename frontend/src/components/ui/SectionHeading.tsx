import type { ReactNode } from 'react'

interface SectionHeadingProps {
  eyebrow?: string
  title: string
  description?: string
  action?: ReactNode
}

export function SectionHeading({ eyebrow, title, description, action }: SectionHeadingProps) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        {eyebrow && (
          <p className="mb-1 text-xs font-medium uppercase tracking-wider text-lime-400/80">{eyebrow}</p>
        )}
        <h2 className="text-lg font-semibold text-[color:var(--color-ink)]">{title}</h2>
        {description && <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">{description}</p>}
      </div>
      {action}
    </div>
  )
}
