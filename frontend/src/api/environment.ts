import type { components } from './types.gen'
import { apiClient } from './client'

export type VegetationSeries = components['schemas']['VegetationSeries']
export type WeatherBundle = components['schemas']['WeatherBundle']

/** `GET /farms/{farm_id}/vegetation` — independent of any analysis run, used for charts. */
export function getVegetation(farmId: string, days = 90) {
  return apiClient.get<VegetationSeries>(`/farms/${farmId}/vegetation?days=${days}`)
}

/** `GET /farms/{farm_id}/weather` — current conditions + daily/hourly forecast, independent of any analysis run. */
export function getWeather(farmId: string, forecastDays = 7) {
  return apiClient.get<WeatherBundle>(`/farms/${farmId}/weather?forecast_days=${forecastDays}`)
}
