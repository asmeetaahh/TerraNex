import { useEffect, useState } from 'react'
import { getVegetation, type VegetationSeries } from '../api/environment'
import { previewVegetation } from '../data/previewFixtures'
import type { DashboardMode } from './useFarmDashboard'

interface LiveState {
  data: VegetationSeries | null
  loading: boolean
  /** True when live mode's real fetch failed — data is intentionally left `null` rather than backfilled with fixture values, so a real farm never shows fabricated NDVI. */
  unavailable: boolean
}

/**
 * `GET /farms/{farm_id}/vegetation` is independent of the analysis run, so it's
 * fetched separately here rather than folded into `useFarmDashboard`. Mirrors
 * that hook's preview/live split: preview farms get the typed fixture (derived
 * synchronously, no effect needed), live farms get the real endpoint, and a
 * failure on a live farm never falls back to fixture data.
 */
export function useVegetation(farmId: string | null, mode: DashboardMode): LiveState {
  const [live, setLive] = useState<LiveState>({ data: null, loading: true, unavailable: false })

  useEffect(() => {
    if (mode !== 'live' || !farmId) return

    let cancelled = false

    getVegetation(farmId)
      .then((series) => {
        if (!cancelled) setLive({ data: series, loading: false, unavailable: false })
      })
      .catch(() => {
        if (!cancelled) setLive({ data: null, loading: false, unavailable: true })
      })

    return () => {
      cancelled = true
    }
  }, [farmId, mode])

  if (mode === 'preview') {
    return { data: farmId ? (previewVegetation[farmId] ?? null) : null, loading: false, unavailable: false }
  }

  if (mode !== 'live' || !farmId) {
    return { data: null, loading: mode === 'loading', unavailable: false }
  }

  return live
}
