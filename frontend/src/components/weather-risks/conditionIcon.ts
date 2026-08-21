import { Cloud, CloudRain, CloudSun, Sun } from 'lucide-react'

/** Maps the free-text `condition` key (WeatherCurrent/WeatherDaily) to a thin-line icon via plain lookup. */
export function conditionIconKey(condition: string | null | undefined): 'rain' | 'clear' | 'cloudy' | 'default' {
  if (!condition) return 'default'
  if (condition.includes('rain')) return 'rain'
  if (condition === 'clear') return 'clear'
  if (condition.includes('cloud')) return 'cloudy'
  return 'default'
}

export const CONDITION_ICONS = {
  rain: CloudRain,
  clear: Sun,
  cloudy: CloudSun,
  default: Cloud,
} as const
