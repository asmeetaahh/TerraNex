import { useEffect, useState } from 'react'
import { getEnumCatalog, type EnumCatalog } from '../api/reference'
import { ApiError } from '../api/client'

interface EnumCatalogState {
  data: EnumCatalog | null
  loading: boolean
  error: ApiError | Error | null
}

/**
 * Loads the real `/reference/enums` catalog — the one live, non-501 endpoint
 * this dashboard can call today. Backs display labels for score bands, risk
 * levels, and advisory categories/priorities instead of hardcoding them.
 */
export function useEnumCatalog(): EnumCatalogState {
  const [state, setState] = useState<EnumCatalogState>({ data: null, loading: true, error: null })

  useEffect(() => {
    let cancelled = false

    getEnumCatalog()
      .then((response) => {
        if (!cancelled) setState({ data: response.enums, loading: false, error: null })
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({ data: null, loading: false, error: error as ApiError | Error })
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  return state
}

export function findEnumLabel(catalog: EnumCatalog | null, group: keyof EnumCatalog, value: string): string | null {
  if (!catalog) return null
  const entry = catalog[group]?.find((item) => item.value === value)
  return entry?.label ?? null
}
