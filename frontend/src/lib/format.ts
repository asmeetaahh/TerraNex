export function relativeTime(iso: string): string {
  const diffMs = Date.parse(iso) - Date.now()
  const diffMin = Math.round(diffMs / 60_000)
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

  const abs = Math.abs(diffMin)
  if (abs < 60) return formatter.format(diffMin, 'minute')
  const diffHour = Math.round(diffMin / 60)
  if (Math.abs(diffHour) < 24) return formatter.format(diffHour, 'hour')
  const diffDay = Math.round(diffHour / 24)
  return formatter.format(diffDay, 'day')
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en', { month: 'short', day: 'numeric' })
}

export function greetingForHour(hour: number): string {
  if (hour < 5) return 'Good night'
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}
