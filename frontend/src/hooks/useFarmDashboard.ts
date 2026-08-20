import { useEffect, useState } from 'react'
import { ApiError } from '../api/client'
import { getFarmDashboard, listFarms, type Farm, type FarmDashboard } from '../api/farms'
import { previewDashboards, previewFarms } from '../data/previewFixtures'

export type DashboardMode = 'loading' | 'preview' | 'live' | 'error'

interface FarmDashboardState {
  mode: DashboardMode
  farms: Farm[]
  selectedFarmId: string | null
  dashboard: FarmDashboard | null
  error: ApiError | Error | null
}

/**
 * Calls the real, typed API client first. `GET /farms` is `501
 * NOT_IMPLEMENTED` in the running backend today, so this settles into
 * `preview` mode and renders the typed fixtures — the moment the backend
 * ships farms, this same code path serves live data with no changes here.
 */
export function useFarmDashboard() {
  const [state, setState] = useState<FarmDashboardState>({
    mode: 'loading',
    farms: [],
    selectedFarmId: null,
    dashboard: null,
    error: null,
  })

  useEffect(() => {
    let cancelled = false

    listFarms()
      .then((list) => {
        if (cancelled) return
        setState((prev) => ({
          ...prev,
          mode: 'live',
          farms: list.items,
          selectedFarmId: list.items[0]?.id ?? null,
        }))
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (error instanceof ApiError && error.code === 'NOT_IMPLEMENTED') {
          setState((prev) => ({
            ...prev,
            mode: 'preview',
            farms: previewFarms,
            selectedFarmId: previewFarms[0]?.id ?? null,
          }))
        } else {
          setState((prev) => ({ ...prev, mode: 'error', error: error as ApiError | Error }))
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (state.mode !== 'live' || !state.selectedFarmId) return

    let cancelled = false
    getFarmDashboard(state.selectedFarmId)
      .then((dashboard) => {
        if (!cancelled) setState((prev) => ({ ...prev, dashboard }))
      })
      .catch((error: unknown) => {
        if (!cancelled) setState((prev) => ({ ...prev, error: error as ApiError | Error }))
      })
    return () => {
      cancelled = true
    }
  }, [state.selectedFarmId, state.mode])

  function selectFarm(farmId: string) {
    setState((prev) => ({ ...prev, selectedFarmId: farmId, dashboard: null }))
  }

  const dashboard =
    state.mode === 'preview' && state.selectedFarmId ? (previewDashboards[state.selectedFarmId] ?? null) : state.dashboard

  return { ...state, dashboard, selectFarm }
}
