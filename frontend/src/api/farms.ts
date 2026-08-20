import type { components } from './types.gen'
import { apiClient } from './client'

export type Farm = components['schemas']['Farm']
export type FarmList = components['schemas']['FarmList']
export type FarmDashboard = components['schemas']['FarmDashboard']

export function listFarms() {
  return apiClient.get<FarmList>('/farms')
}

export function getFarmDashboard(farmId: string) {
  return apiClient.get<FarmDashboard>(`/farms/${farmId}/dashboard`)
}
