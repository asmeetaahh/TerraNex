import { AlertTriangle, Droplet, Leaf, Mountain } from 'lucide-react'
import type { components } from '../../api/types.gen'
import type { EnumCatalog } from '../../api/reference'
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

interface FactorCard {
  key: string
  label: string
  icon: typeof Leaf
  iconTint: string
  status: Status
  headline: string
  subtitle: string
  detail: string
  score: number
}

/** Detail cards for the four factors behind the Farm Health Score — same visual language as the Dashboard's score row, with one extra real detail line per factor. */
export function HealthFactorCards({ analysis, enums }: { analysis: AnalysisRun; enums: EnumCatalog | null }) {
  const cropStatus = statusFromScoreBand(analysis.crop_health.band)
  const waterStatus = statusFromRiskLevel(analysis.water_risk.level)
  const diseaseStatus = statusFromRiskLevel(analysis.disease_risk.level)
  const soilStatus = statusFromScoreBand(analysis.soil_assessment.band)

  const cropDetail = analysis.crop_health.growth_stage
    ? `Growth stage: ${titleCase(analysis.crop_health.growth_stage)}`
    : analysis.crop_health.ndvi_trend
      ? `NDVI trend: ${titleCase(analysis.crop_health.ndvi_trend)}`
      : 'No growth-stage data yet'

  const waterDetail =
    analysis.water_risk.days_until_stress != null
      ? `${analysis.water_risk.days_until_stress}d until stress · ${Math.round(analysis.water_risk.deficit_mm)}mm deficit`
      : `${Math.round(analysis.water_risk.recommended_irrigation_mm)}mm recommended`

  const diseaseDetail =
    analysis.disease_risk.risks && analysis.disease_risk.risks.length > 0
      ? `${analysis.disease_risk.risks.length} pathogen${analysis.disease_risk.risks.length > 1 ? 's' : ''} tracked`
      : 'No active pathogen pressure'

  const soilDetail = analysis.soil_assessment.ph_status
    ? `pH: ${titleCase(analysis.soil_assessment.ph_status)}`
    : (analysis.soil_assessment.texture_class ?? 'No soil detail yet')

  const cards: FactorCard[] = [
    {
      key: 'crop_health',
      label: 'Crop Health',
      icon: Leaf,
      iconTint: 'text-lime-400 bg-lime-500/10',
      status: cropStatus,
      headline: `${Math.round(analysis.crop_health.score)}/100`,
      subtitle: findEnumLabel(enums, 'score_band', analysis.crop_health.band) ?? titleCase(analysis.crop_health.band),
      detail: cropDetail,
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
      detail: waterDetail,
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
      detail: diseaseDetail,
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
      detail: soilDetail,
      score: analysis.soil_assessment.score,
    },
  ]

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {cards.map((card) => {
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
            <p className="truncate border-t border-white/[0.06] pt-2 text-[11px] text-[color:var(--color-ink-faint)]">
              {card.detail}
            </p>
          </Card>
        )
      })}
    </div>
  )
}
