import type { components } from './types.gen'
import { apiClient } from './client'

export type EnumCatalog = components['schemas']['EnumCatalog']

export function getEnumCatalog() {
  return apiClient.get<components['schemas']['EnumCatalogResponse']>('/reference/enums')
}
