import { AlertTriangle, Droplet, Leaf, Mountain } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
import mascot from '../../assets/TerraNex-mascot.png'
import { Card } from '../ui/Card'
import { Sparkline } from '../ui/Sparkline'
import { findEnumLabel } from '../../hooks/useEnumCatalog'
import { STATUS_COLOR, statusFromRiskLevel, statusFromScoreBand, titleCase, type Status } from '../../lib/status'

type AnalysisRun = components['schemas']['AnalysisRun']

const WATER_MICROCOPY: Record<Status, string> = {
  good: 'Low risk',
  warning: 'Monitor closely',
  serious: 'Irrigate soon',
  critical: 'Urgent deficit',
}

const DISEASE_MICROCOPY: Record<Status, string> = {
  good: 'Low risk',
  warning: 'Watch closely',
  serious: 'High pressure',
  critical: 'Act immediately',
}

const SOIL_MICROCOPY: Record<Status, string> = {
  good: 'Balanced',
  warning: 'Needs attention',
  serious: 'Degraded',
  critical: 'Critical',
}

interface SecondaryCard {
  key: string
  label: string
  icon: typeof Leaf
  iconTint: string
  status: Status
  headline: string
  subtitle: string
  score: number
}

export function HealthCardsGrid({ analysis, enums }: { analysis: AnalysisRun; enums: EnumCatalog | null }) {
  const heroStatus = statusFromScoreBand(analysis.overall_band)
  const heroBandLabel = findEnumLabel(enums, 'score_band', analysis.overall_band) ?? titleCase(analysis.overall_band)

  const waterStatus = statusFromRiskLevel(analysis.water_risk.level)
  const diseaseStatus = statusFromRiskLevel(analysis.disease_risk.level)
  const soilStatus = statusFromScoreBand(analysis.soil_assessment.band)
  const cropStatus = statusFromScoreBand(analysis.crop_health.band)

  const secondaryCards: SecondaryCard[] = [
    {
      key: 'crop_health',
      label: 'Crop Health',
      icon: Leaf,
      iconTint: 'text-lime-400 bg-lime-500/10',
      status: cropStatus,
      headline: `${Math.round(analysis.crop_health.score)}/100`,
      subtitle: findEnumLabel(enums, 'score_band', analysis.crop_health.band) ?? titleCase(analysis.crop_health.band),
      score: analysis.crop_health.score,
    },
    {
      key: 'water_risk',
      label: 'Water Risk',
      icon: Droplet,
      iconTint: 'text-sky-300 bg-sky-400/10',
      status: waterStatus,
      headline: findEnumLabel(enums, 'risk_level', analysis.water_risk.level) ?? titleCase(analysis.water_risk.level),
      subtitle: WATER_MICROCOPY[waterStatus],
      score: analysis.water_risk.score,
    },
    {
      key: 'disease_risk',
      label: 'Disease Risk',
      icon: AlertTriangle,
      iconTint: 'text-amber-300 bg-amber-400/10',
      status: diseaseStatus,
      headline: findEnumLabel(enums, 'risk_level', analysis.disease_risk.level) ?? titleCase(analysis.disease_risk.level),
      subtitle: DISEASE_MICROCOPY[diseaseStatus],
      score: analysis.disease_risk.score,
    },
    {
      key: 'soil_health',
      label: 'Soil Health',
      icon: Mountain,
      iconTint: 'text-lime-400 bg-lime-500/10',
      status: soilStatus,
      headline: findEnumLabel(enums, 'score_band', analysis.soil_assessment.band) ?? titleCase(analysis.soil_assessment.band),
      subtitle: SOIL_MICROCOPY[soilStatus],
      score: analysis.soil_assessment.score,
    },
  ]

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
      <Card glow className="flex items-center gap-4 overflow-hidden p-5 sm:col-span-2">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-[color:var(--color-ink-muted)]">Farm Health Score</p>
          <p className="mt-1.5 flex items-baseline gap-1">
            <span className="text-4xl font-semibold tabular-nums text-[color:var(--color-ink)]">
              {Math.round(analysis.overall_health_score)}
            </span>
            <span className="text-sm text-[color:var(--color-ink-faint)]">/100</span>
          </p>
          <p className="mt-1.5 truncate text-sm font-medium" style={{ color: STATUS_COLOR[heroStatus] }}>
            {heroBandLabel}
          </p>
        </div>
        <div className="relative flex h-24 w-24 shrink-0 items-center justify-center">
          <svg viewBox="0 0 100 100" className="absolute inset-0 -rotate-90">
            <circle cx="50" cy="50" r="42" fill="none" stroke="var(--color-border)" strokeWidth="7" />
            <circle
              cx="50"
              cy="50"
              r="42"
              fill="none"
              stroke={STATUS_COLOR[heroStatus]}
              strokeWidth="7"
              strokeLinecap="round"
              strokeDasharray={2 * Math.PI * 42}
              strokeDashoffset={2 * Math.PI * 42 * (1 - analysis.overall_health_score / 100)}
              style={{ filter: `drop-shadow(0 0 8px color-mix(in oklab, ${STATUS_COLOR[heroStatus]} 65%, transparent))` }}
              className="transition-[stroke-dashoffset] duration-700 ease-out"
            />
          </svg>
          <img src={mascot} alt="" aria-hidden="true" className="h-14 w-14 object-contain" />
        </div>
      </Card>

      {secondaryCards.map((card) => {
        const Icon = card.icon
        return (
          <Card key={card.key} className="flex min-w-0 flex-col gap-2 p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-[color:var(--color-ink-muted)]">{card.label}</span>
              <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${card.iconTint}`}>
                <Icon size={12} strokeWidth={2} />
              </span>
            </div>
            <p className="truncate text-xl font-semibold sm:text-2xl" style={{ color: STATUS_COLOR[card.status] }}>
              {card.headline}
            </p>
            <p className="truncate text-xs font-medium" style={{ color: STATUS_COLOR[card.status] }}>
              {card.subtitle}
            </p>
            <Sparkline score={card.score} status={card.status} width={90} height={24} />
          </Card>
        )
      })}
    </div>
  )
}
