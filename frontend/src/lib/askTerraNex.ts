import type { components } from '../api/types.gen'

type Advisory = components['schemas']['Advisory']
type ScoredFactor = components['schemas']['ScoredFactor']
type RegenerativeRecommendation = components['schemas']['RegenerativeRecommendation']

export interface AskTerraNexData {
  advisories: Advisory[]
  factors: ScoredFactor[]
  regenerative: RegenerativeRecommendation[]
}

export interface AskTerraNexResponse {
  text: string
  signals: ScoredFactor[]
}

type CategoryId = 'water' | 'heat' | 'crop_health' | 'disease' | 'soil_planting'

const CATEGORY_KEYWORDS: Record<CategoryId, string[]> = {
  water: ['water', 'irrigat', 'drought', 'moisture', 'dry'],
  heat: ['heat', 'hot', 'temperature', 'warm'],
  crop_health: ['crop health', 'vigour', 'vigor', 'yellow', 'weak', 'leaves', 'leaf', 'ndvi'],
  disease: ['disease', 'pest', 'fungal', 'infection', 'blight'],
  soil_planting: ['plant', 'soil', 'season', 'rotation', 'sow'],
}

function matchCategory(question: string): CategoryId | null {
  const lower = question.toLowerCase()
  for (const [id, keywords] of Object.entries(CATEGORY_KEYWORDS) as [CategoryId, string[]][]) {
    if (keywords.some((kw) => lower.includes(kw))) return id
  }
  return null
}

function findFactor(factors: ScoredFactor[], match: RegExp): ScoredFactor | undefined {
  return factors.find((f) => match.test(f.key) || match.test(f.label))
}

function adviceLine(advisory: Advisory): string {
  const window = advisory.action_window ? ` (${advisory.action_window})` : ''
  return ` TerraNex recommends: ${advisory.title}${window}. ${advisory.rationale}`
}

function respondWater(data: AskTerraNexData): AskTerraNexResponse {
  const factor = findFactor(data.factors, /water/i)
  const advisory = data.advisories.find((a) => a.category === 'irrigation') ?? data.advisories.find((a) => /water|irrigat/i.test(a.title))

  let text = factor
    ? `Water availability is currently your most urgent signal. ${factor.explanation}`
    : `TerraNex doesn't have a dedicated water-balance signal for this farm yet.`
  if (advisory) text += adviceLine(advisory)

  return { text, signals: factor ? [factor] : [] }
}

function respondHeat(data: AskTerraNexData): AskTerraNexResponse {
  const advisory = data.advisories.find((a) => /heat/i.test(a.title)) ?? data.advisories.find((a) => a.category === 'weather')
  const factor = findFactor(data.factors, /weather/i)

  let text: string
  if (advisory) {
    text = `${advisory.title}. ${advisory.body}`
    if (advisory.rationale) text += ` Why: ${advisory.rationale}`
  } else if (factor) {
    text = `Weather risk is currently scoring ${Math.round(factor.score)}/100 (${factor.band}). ${factor.explanation}`
  } else {
    text = `TerraNex doesn't have an active heat or weather advisory for this farm right now.`
  }

  return { text, signals: factor ? [factor] : [] }
}

function respondCropHealth(data: AskTerraNexData): AskTerraNexResponse {
  const factor = findFactor(data.factors, /crop.?health|vigou?r/i)
  const advisory = data.advisories.find((a) => a.category === 'pest' || a.category === 'disease' || a.category === 'nutrient')

  let text = factor
    ? `Crop health is currently scoring ${Math.round(factor.score)}/100 (${factor.band}). ${factor.explanation}`
    : `TerraNex doesn't have a dedicated crop-health signal for this farm yet.`

  if (advisory) {
    text += adviceLine(advisory)
  } else if (factor && (factor.band === 'poor' || factor.band === 'critical')) {
    text += ` There isn't an open advisory for this yet, but it's worth monitoring closely.`
  }

  return { text, signals: factor ? [factor] : [] }
}

function respondDisease(data: AskTerraNexData): AskTerraNexResponse {
  const factor = findFactor(data.factors, /disease/i)
  const advisory = data.advisories.find((a) => a.category === 'disease' || a.category === 'pest')

  let text = factor
    ? `Disease pressure is currently scoring ${Math.round(factor.score)}/100 (${factor.band}). ${factor.explanation}`
    : `TerraNex doesn't have a dedicated disease-pressure signal for this farm yet.`

  if (advisory) text += adviceLine(advisory)

  return { text, signals: factor ? [factor] : [] }
}

function respondSoilPlanting(data: AskTerraNexData): AskTerraNexResponse {
  const factor = findFactor(data.factors, /soil/i)
  const rec = [...data.regenerative].sort((a, b) => b.relevance_score - a.relevance_score)[0]

  let text = factor
    ? `Your soil signal: ${factor.explanation}`
    : `TerraNex doesn't have a dedicated soil signal for this farm yet.`

  if (rec) {
    text += ` For next steps, TerraNex's top regenerative recommendation is ${rec.practice_name} — ${rec.description}`
  }

  return { text, signals: factor ? [factor] : [] }
}

function respondFallback(data: AskTerraNexData): AskTerraNexResponse {
  const topAdvisory = [...data.advisories].sort(
    (a, b) =>
      { const order: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 }; return order[a.priority] - order[b.priority] },
  )[0]

  if (topAdvisory) {
    return {
      text: `I don't have a specific match for that yet, but here's what stands out right now: ${topAdvisory.title}. ${topAdvisory.body}`,
      signals: [],
    }
  }

  if (data.factors.length > 0) {
    const worst = [...data.factors].sort((a, b) => a.score - b.score)[0]
    return {
      text: `I don't have a specific match for that yet, but your lowest-scoring signal right now is ${worst.label} at ${Math.round(worst.score)}/100. ${worst.explanation}`,
      signals: [worst],
    }
  }

  return { text: `TerraNex doesn't have any active signals for this farm yet.`, signals: [] }
}

/** Composes a conversational preview response from the SAME advisory/factor/regenerative data already shown elsewhere on the page — no invented farm data. */
export function buildAskTerraNexResponse(question: string, data: AskTerraNexData): AskTerraNexResponse {
  const category = matchCategory(question)

  switch (category) {
    case 'water':
      return respondWater(data)
    case 'heat':
      return respondHeat(data)
    case 'crop_health':
      return respondCropHealth(data)
    case 'disease':
      return respondDisease(data)
    case 'soil_planting':
      return respondSoilPlanting(data)
    default:
      return respondFallback(data)
  }
}
