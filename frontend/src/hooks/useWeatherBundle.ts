import { useEffect, useState } from 'react'
import { getWeather, type WeatherBundle } from '../api/environment'
import { previewWeatherBundle } from '../data/previewFixtures'
import type { DashboardMode } from './useFarmDashboard'

interface LiveState {
  data: WeatherBundle | null
  loading: boolean
  /** True when live mode's real fetch failed — data is intentionally left `null` rather than backfilled with fixture values, so a real farm never shows fabricated conditions. */
  unavailable: boolean
}

/**
 * `GET /farms/{farm_id}/weather` is independent of the analysis run, so it's
 * fetched separately here — same preview/live split as `useVegetation`.
 */
export function useWeatherBundle(farmId: string | null, mode: DashboardMode): LiveState {
  const [live, setLive] = useState<LiveState>({ data: null, loading: true, unavailable: false })

  useEffect(() => {
    if (mode !== 'live' || !farmId) return

    let cancelled = false

    getWeather(farmId)
      .then((bundle) => {
        if (!cancelled) setLive({ data: bundle, loading: false, unavailable: false })
      })
      .catch(() => {
        if (!cancelled) setLive({ data: null, loading: false, unavailable: true })
      })

    return () => {
      cancelled = true
    }
  }, [farmId, mode])

  if (mode === 'preview') {
    return { data: farmId ? (previewWeatherBundle[farmId] ?? null) : null, loading: false, unavailable: false }
  }

  if (mode !== 'live' || !farmId) {
    return { data: null, loading: mode === 'loading', unavailable: false }
  }

  return live
}
