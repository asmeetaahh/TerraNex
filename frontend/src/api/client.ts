import type { components } from './types.gen'

type ErrorDetail = components['schemas']['ErrorDetail']

const BASE_URL = import.meta.env.VITE_API_BASE_URL as string

/** Thrown for every non-2xx response. Mirrors the API's single error envelope. */
export class ApiError extends Error {
  code: string
  details: ErrorDetail['details']
  requestId: string
  status: number

  constructor(status: number, error: ErrorDetail) {
    super(error.message)
    this.name = 'ApiError'
    this.status = status
    this.code = error.code
    this.details = error.details
    this.requestId = error.request_id
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...init?.headers,
    },
  })

  if (response.status === 204) {
    return undefined as T
  }

  const body = await response.json()

  if (!response.ok) {
    throw new ApiError(response.status, body.error as ErrorDetail)
  }

  return body as T
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, jsonInit('POST', body)),
  patch: <T>(path: string, body?: unknown) => request<T>(path, jsonInit('PATCH', body)),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** For multipart uploads (crop images) — do not set Content-Type, the browser sets the boundary. */
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: 'POST', body: form }),
}
