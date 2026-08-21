import { Bird, Droplets, Layers, Mountain, TriangleAlert } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { STATUS_COLOR, statusFromScoreBand, titleCase, type Status } from '../../lib/status'

type AnalysisRun = components['schemas']['AnalysisRun']

interface IndicatorCard {
  key: string
  label: string
  icon: typeof Layers
  iconTint: string
  headline: string
  status?: Status
  detail: string
  tracked: boolean
}

/**
 * Five signals behind this farm's regenerative potential. Three are real,
 * already-fetched analysis fields (organic carbon sub-factor, soil
 * assessment, water-holding capacity). Biodiversity and erosion risk have no
 * backend signal yet — shown honestly as "not tracked" rather than a made-up
 * score.
 */
export function KeyIndicators({ analysis }: { analysis: AnalysisRun }) {
  const organicCarbonFactor = analysis.soil_assessment.factors?.find((f) => f.key === 'organic_carbon')
  const waterHoldingMm = analysis.water_risk.water_holding_capacity_mm

  const soilDetail =
    [
      analysis.soil_assessment.organic_matter_status && `Organic matter: ${titleCase(analysis.soil_assessment.organic_matter_status)}`,
      analysis.soil_assessment.fertility_status && `Fertility: ${titleCase(analysis.soil_assessment.fertility_status)}`,
    ]
      .filter((line): line is string => Boolean(line))
      .join(' · ') || analysis.soil_assessment.explanation

  const cards: IndicatorCard[] = [
    {
      key: 'organic_carbon',
      label: 'Soil organic carbon',
      icon: Layers,
      iconTint: 'text-lime-400 bg-lime-500/10',
      headline: organicCarbonFactor ? `${Math.round(organicCarbonFactor.score)}/100` : 'Not tracked',
      status: organicCarbonFactor ? statusFromScoreBand(organicCarbonFactor.band) : undefined,
      detail: organicCarbonFactor?.explanation ?? 'Not available for this farm yet.',
      tracked: Boolean(organicCarbonFactor),
    },
    {
      key: 'soil_health',
      label: 'Soil health',
      icon: Mountain,
      iconTint: 'text-amber-300 bg-amber-400/10',
      headline: `${Math.round(analysis.soil_assessment.score)}/100`,
      status: statusFromScoreBand(analysis.soil_assessment.band),
      detail: soilDetail,
      tracked: true,
    },
    {
      key: 'water_resilience',
      label: 'Water resilience',
      icon: Droplets,
      iconTint: 'text-sky-300 bg-sky-400/10',
      headline: waterHoldingMm != null ? `${Math.round(waterHoldingMm)} mm` : 'Not tracked',
      detail:
        waterHoldingMm != null
          ? 'Plant-available water the soil can buffer between rain or irrigation events.'
          : 'Not available for this farm yet.',
      tracked: waterHoldingMm != null,
    },
    {
      key: 'biodiversity',
      label: 'Biodiversity',
      icon: Bird,
      iconTint: 'text-[color:var(--color-ink-faint)] bg-white/[0.04]',
      headline: 'Not tracked',
      detail: "TerraNex doesn't compute a biodiversity signal for this farm yet.",
      tracked: false,
    },
    {
      key: 'erosion_risk',
      label: 'Erosion risk',
      icon: TriangleAlert,
      iconTint: 'text-[color:var(--color-ink-faint)] bg-white/[0.04]',
      headline: 'Not tracked',
      detail: "TerraNex doesn't compute an erosion signal for this farm yet.",
      tracked: false,
    },
  ]

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold text-[color:var(--color-ink)]">Key Indicators</h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">The measured signals behind this farm's regenerative potential</p>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {cards.map((card) => {
          const Icon = card.icon
          const color = card.status ? STATUS_COLOR[card.status] : 'var(--color-ink-faint)'
          return (
            <Card key={card.key} className={`flex min-w-0 flex-col gap-2 p-4 ${card.tracked ? '' : 'opacity-70'}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-medium text-[color:var(--color-ink-muted)]">{card.label}</span>
                <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${card.iconTint}`}>
                  <Icon size={12} strokeWidth={2} />
                </span>
              </div>
              <p className="truncate text-xl font-semibold sm:text-2xl" style={{ color: card.tracked ? color : 'var(--color-ink-faint)' }}>
                {card.headline}
              </p>
              <p className="text-[11px] leading-relaxed text-[color:var(--color-ink-faint)]">{card.detail}</p>
            </Card>
          )
        })}
      </div>
    </section>
  )
}
