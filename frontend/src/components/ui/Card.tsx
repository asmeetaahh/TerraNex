import type { ComponentPropsWithoutRef } from 'react'
import { cn } from '../../lib/cn'

interface CardProps extends ComponentPropsWithoutRef<'div'> {
  glow?: boolean
}

/** The refined glass/charcoal card surface used throughout the dashboard. */
export function Card({ className, glow, children, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-2xl border border-white/[0.06] bg-[color:var(--color-surface)]/80 backdrop-blur-sm',
        'shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]',
        glow && 'glow-lime',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}
