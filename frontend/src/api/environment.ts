import type { components } from './types.gen'
import { apiClient } from './client'

export type VegetationSeries = components['schemas']['VegetationSeries']

/** `GET /farms/{farm_id}/vegetation` — independent of any analysis run, used for charts. */
export function getVegetation(farmId: string, days = 90) {
  return apiClient.get<VegetationSeries>(`/farms/${farmId}/vegetation?days=${days}`)
}
