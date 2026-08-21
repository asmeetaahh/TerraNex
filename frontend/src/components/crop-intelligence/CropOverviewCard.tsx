import { CalendarDays, Sprout, Wheat } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import { Card } from '../ui/Card'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_COLOR, statusFromScoreBand, titleCase } from '../../lib/status'
import { shortDate } from '../../lib/format'

type FarmCrop = components['schemas']['FarmCrop']
type CropHealth = components['schemas']['CropHealth']

/** The registered crop (if any) plus the latest run's crop-health snapshot — the "what am I growing, and how is it doing" summary. */
export function CropOverviewCard({
  crops,
  cropHealth,
  enums,
}: {
  crops: FarmCrop[]
  cropHealth: CropHealth
  enums: EnumCatalog | null
}) {
  const primary = crops.find((crop) => crop.is_primary) ?? crops[0] ?? null
  const status = statusFromScoreBand(cropHealth.band)
  const color = STATUS_COLOR[status]
  const bandLabel = findEnumLabel(enums, 'score_band', cropHealth.band) ?? titleCase(cropHealth.band)
  const growthStageLabel = cropHealth.growth_stage
    ? titleCase(cropHealth.growth_stage)
    : primary
      ? (findEnumLabel(enums, 'growth_stage', primary.growth_stage) ?? titleCase(primary.growth_stage))
      : null

  return (
    <Card glow className="p-6 sm:p-7">
      <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center">
        <div className="relative flex h-28 w-28 shrink-0 items-center justify-center">
          <svg viewBox="0 0 100 100" className="absolute inset-0 -rotate-90">
            <circle cx="50" cy="50" r="42" fill="none" stroke="var(--color-border)" strokeWidth="8" />
            <circle
              cx="50"
              cy="50"
              r="42"
              fill="none"
              stroke={color}
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={2 * Math.PI * 42}
              strokeDashoffset={2 * Math.PI * 42 * (1 - cropHealth.score / 100)}
              style={{ filter: `drop-shadow(0 0 8px color-mix(in oklab, ${color} 65%, transparent))` }}
              className="transition-[stroke-dashoffset] duration-700 ease-out"
            />
          </svg>
          <div className="flex flex-col items-center">
            <span className="text-2xl font-semibold tabular-nums text-[color:var(--color-ink)]">
              {Math.round(cropHealth.score)}
            </span>
            <span className="text-[10px] text-[color:var(--color-ink-faint)]">/100</span>
          </div>
        </div>

        <div className="min-w-0 flex-1 text-center sm:text-left">
          <p className="text-xs font-medium tracking-wide text-[color:var(--color-ink-muted)] uppercase">Current Crop</p>
          <div className="mt-1 flex flex-wrap items-center justify-center gap-2 sm:justify-start">
            <span className="flex items-center gap-2 text-xl font-semibold text-[color:var(--color-ink)]">
              <Wheat size={18} strokeWidth={1.5} className="text-lime-400" />
              {primary ? primary.crop.name : 'No crop registered'}
            </span>
            <span className="text-lg font-semibold" style={{ color }}>
              {bandLabel}
            </span>
          </div>

          <div className="mt-3 flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 text-xs text-[color:var(--color-ink-muted)] sm:justify-start">
            {growthStageLabel && (
              <span className="flex items-center gap-1.5">
                <Sprout size={13} strokeWidth={1.5} className="text-[color:var(--color-ink-faint)]" />
                {growthStageLabel}
              </span>
            )}
            {cropHealth.days_since_planting != null && (
              <span className="flex items-center gap-1.5">
                <CalendarDays size={13} strokeWidth={1.5} className="text-[color:var(--color-ink-faint)]" />
                {cropHealth.days_since_planting}d since planting
              </span>
            )}
            {cropHealth.days_to_expected_harvest != null && (
              <span className="flex items-center gap-1.5">
                <CalendarDays size={13} strokeWidth={1.5} className="text-[color:var(--color-ink-faint)]" />
                {cropHealth.days_to_expected_harvest}d to harvest
              </span>
            )}
            {primary?.expected_harvest_date && (
              <span className="text-[color:var(--color-ink-faint)]">Est. harvest {shortDate(primary.expected_harvest_date)}</span>
            )}
          </div>

          <p className="mx-auto mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--color-ink-muted)] sm:mx-0">
            {cropHealth.explanation}
          </p>
        </div>
      </div>
    </Card>
  )
}
