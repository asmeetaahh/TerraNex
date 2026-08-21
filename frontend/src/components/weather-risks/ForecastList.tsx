import { Flame } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { CONDITION_ICONS, conditionIconKey } from './conditionIcon'

type WeatherDaily = components['schemas']['WeatherDaily']

const HEAT_THRESHOLD_C = 30

function dayLabel(dateStr: string, index: number): string {
  if (index === 0) return 'Today'
  const date = new Date(`${dateStr}T00:00:00`)
  return date.toLocaleDateString('en', { weekday: 'short' })
}

export function ForecastList({ days }: { days: WeatherDaily[] }) {
  if (days.length === 0) {
    return (
      <Card className="p-5">
        <p className="text-sm text-[color:var(--color-ink-faint)]">No forecast available for this farm.</p>
      </Card>
    )
  }

  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-7">
      {days.map((day, index) => {
        const Icon = CONDITION_ICONS[conditionIconKey(day.condition)]
        const isHot = day.temp_max_c != null && day.temp_max_c >= HEAT_THRESHOLD_C
        return (
          <Card key={day.date} className="flex flex-col items-center gap-1.5 p-3 text-center">
            <p className="text-xs font-medium text-[color:var(--color-ink-muted)]">{dayLabel(day.date, index)}</p>
            <Icon size={22} strokeWidth={1.5} className="text-lime-400" />
            <p className="text-sm font-semibold text-[color:var(--color-ink)]">
              {day.temp_max_c != null ? Math.round(day.temp_max_c) : '—'}°
              <span className="ml-1 text-xs font-normal text-[color:var(--color-ink-faint)]">
                {day.temp_min_c != null ? Math.round(day.temp_min_c) : '—'}°
              </span>
            </p>
            <p className="text-[11px] text-[color:var(--color-ink-faint)]">
              {day.precipitation_mm != null ? `${day.precipitation_mm} mm` : '—'}
            </p>
            {day.wind_max_kmh != null && (
              <p className="text-[10px] text-[color:var(--color-ink-faint)]">{Math.round(day.wind_max_kmh)} km/h wind</p>
            )}
            {isHot && (
              <span className="mt-0.5 flex items-center gap-1 rounded-full bg-[color:var(--color-status-serious)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[color:var(--color-status-serious)]">
                <Flame size={10} strokeWidth={2} />
                Heat
              </span>
            )}
          </Card>
        )
      })}
    </div>
  )
}
