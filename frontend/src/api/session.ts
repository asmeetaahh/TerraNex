/**
 * Reads the current Supabase session token, if one exists, without adding the
 * Supabase SDK as a dependency — `@supabase/supabase-js` persists its session
 * to `localStorage` under `sb-<project-ref>-auth-token` by default, so this
 * just reads that convention directly.
 *
 * `VITE_SUPABASE_URL` is empty while `ENABLE_AUTH=false` on the backend (see
 * `.env.local`), so this resolves to `null` and every request is sent
 * unauthenticated, exactly as today — it activates automatically once a real
 * login flow starts writing a session and the backend switches auth on.
 */

interface StoredSupabaseSession {
  access_token?: string
  currentSession?: { access_token?: string }
}

function storageKey(): string | null {
  const url = import.meta.env.VITE_SUPABASE_URL as string | undefined
  if (!url) return null

  try {
    const projectRef = new URL(url).hostname.split('.')[0]
    return projectRef ? `sb-${projectRef}-auth-token` : null
  } catch {
    return null
  }
}

export function getAccessToken(): string | null {
  const key = storageKey()
  if (!key) return null

  const raw = localStorage.getItem(key)
  if (!raw) return null

  try {
    const parsed = JSON.parse(raw) as StoredSupabaseSession
    return parsed.access_token ?? parsed.currentSession?.access_token ?? null
  } catch {
    return null
  }
}
