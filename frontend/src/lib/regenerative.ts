import type { components } from '../api/types.gen'

type RegenerativeRecommendation = components['schemas']['RegenerativeRecommendation']

export interface RegenerativeRoadmap {
  now: RegenerativeRecommendation[]
  thisSeason: RegenerativeRecommendation[]
  longTerm: RegenerativeRecommendation[]
}

function bucketTimeframe(text?: string | null): 'now' | 'season' | 'long' {
  if (!text) return 'season'
  const lower = text.toLowerCase()
  if (lower.includes('immediate') || lower.includes('now')) return 'now'
  if (lower.includes('year')) return 'long'
  const numbers = lower.match(/\d+/g)?.map(Number) ?? []
  const max = numbers.length > 0 ? Math.max(...numbers) : null
  if (max !== null && max >= 3) return 'long'
  return 'season'
}

/** Buckets the SAME regenerative_recommendations shown as practice cards by their real `time_to_benefit` field — no separate source of truth. */
export function buildRegenerativeRoadmap(practices: RegenerativeRecommendation[]): RegenerativeRoadmap {
  const roadmap: RegenerativeRoadmap = { now: [], thisSeason: [], longTerm: [] }
  for (const practice of practices) {
    const bucket = bucketTimeframe(practice.time_to_benefit)
    if (bucket === 'now') roadmap.now.push(practice)
    else if (bucket === 'season') roadmap.thisSeason.push(practice)
    else roadmap.longTerm.push(practice)
  }
  return roadmap
}

/**
 * Derived, presentation-only "regenerative potential" score — the average
 * `relevance_score` across this farm's matched practices. Not a backend field;
 * higher means TerraNex's regenerative recommendations apply more strongly to
 * this farm's current soil and management conditions.
 */
export function regenerativePotentialScore(practices: RegenerativeRecommendation[]): number | null {
  if (practices.length === 0) return null
  const total = practices.reduce((sum, practice) => sum + practice.relevance_score, 0)
  return Math.round(total / practices.length)
}
