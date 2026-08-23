import type { components } from './types.gen'
import { apiClient } from './client'

export type CropImage = components['schemas']['CropImage']
export type CropImageAnalysis = components['schemas']['CropImageAnalysis']

interface UploadCropImageOptions {
  /** Ties the photo to a specific planting on the farm. */
  farmCropId?: string
  /** Free-text annotation, max 1000 characters per the contract. */
  note?: string
}

/**
 * `POST /farms/{farm_id}/crop-images` — `multipart/form-data`.
 *
 * Returns with `analysis_status: "pending"`, so the caller can show the stored
 * image before paying for the (slower) vision call. `Content-Type` is left to
 * the browser so it can set the multipart boundary.
 */
export function uploadCropImage(farmId: string, file: File, options: UploadCropImageOptions = {}) {
  const form = new FormData()
  form.append('file', file)
  if (options.farmCropId) form.append('farm_crop_id', options.farmCropId)
  if (options.note) form.append('note', options.note)

  return apiClient.upload<CropImage>(`/farms/${farmId}/crop-images`, form)
}

/**
 * `POST /crop-images/{image_id}/analyze` — runs the diagnosis on an uploaded image.
 *
 * No request body; the farm's crop and weather context is assembled server-side.
 * Returns the same `CropImage` with `analysis` populated.
 */
export function analyzeCropImage(imageId: string) {
  return apiClient.post<CropImage>(`/crop-images/${imageId}/analyze`)
}
