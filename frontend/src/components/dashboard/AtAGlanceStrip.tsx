import { Cloud, CloudRain, Droplet, Droplets, Wind } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'

type WeatherCurrent = components['schemas']['WeatherCurrent']

interface GlanceItem {
  key: string
  icon: typeof Droplet
  iconClass: string
  label: string
  value: string
  status?: string
}

export function AtAGlanceStrip({
  current,
  soilMoisturePct,
}: {
  current: WeatherCurrent | null
  soilMoisturePct: number | null | undefined
}) {
  if (!current) return null

  const items: GlanceItem[] = [
    {
      key: 'temperature',
      icon: Cloud,
      iconClass: 'text-[color:var(--color-ink-muted)]',
      label: 'Temperature',
      value: `${Math.round(current.temperature_c)}°C`,
      status: current.condition_label ?? undefined,
    },
    {
      key: 'humidity',
      icon: Droplet,
      iconClass: 'text-sky-300',
      label: 'Humidity',
      value: current.humidity_pct != null ? `${current.humidity_pct}%` : '—',
    },
    {
      key: 'rainfall',
      icon: CloudRain,
      iconClass: 'text-sky-300',
      label: 'Rainfall',
      value: current.precipitation_mm != null ? `${current.precipitation_mm} mm` : '—',
    },
    {
      key: 'wind',
      icon: Wind,
      iconClass: 'text-lime-400',
      label: 'Wind',
      value: current.wind_speed_kmh != null ? `${Math.round(current.wind_speed_kmh)} km/h` : '—',
    },
    {
      key: 'cloud_cover',
      icon: Cloud,
      iconClass: 'text-amber-300',
      label: 'Cloud Cover',
      value: current.cloud_cover_pct != null ? `${current.cloud_cover_pct}%` : '—',
    },
    {
      key: 'soil_moisture',
      icon: Droplets,
      iconClass: 'text-lime-400',
      label: 'Soil Moisture',
      value: soilMoisturePct != null ? `${Math.round(soilMoisturePct)}%` : '—',
      status: soilMoisturePct != null ? (soilMoisturePct >= 40 ? 'Good' : 'Low') : undefined,
    },
  ]

  return (
    <Card className="p-5">
      <p className="mb-4 text-xs font-medium text-[color:var(--color-ink-muted)]">At a Glance</p>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        {items.map((item) => {
          const Icon = item.icon
          return (
            <div key={item.key} className="flex items-start gap-2">
              <Icon size={18} strokeWidth={1.5} className={`mt-0.5 shrink-0 ${item.iconClass}`} />
              <div className="min-w-0">
                <p className="text-[11px] text-[color:var(--color-ink-faint)]">{item.label}</p>
                <p className="text-sm font-semibold text-[color:var(--color-ink)]">{item.value}</p>
                {item.status && <p className="text-[11px] font-medium text-lime-400">{item.status}</p>}
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
