import { Languages } from 'lucide-react'
import { LANGUAGES, type LanguageCode } from '../../lib/i18n'

export function LanguageSelector({
  value,
  onChange,
}: {
  value: LanguageCode
  onChange: (code: LanguageCode) => void
}) {
  return (
    <label className="relative inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.02] py-1.5 pr-7 pl-3 text-xs font-medium text-[color:var(--color-ink-muted)] transition-colors hover:border-lime-400/30 hover:text-[color:var(--color-ink)]">
      <Languages size={13} strokeWidth={1.5} className="shrink-0 text-lime-400" />
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as LanguageCode)}
        aria-label="Interface language"
        className="cursor-pointer appearance-none bg-transparent pr-1 text-xs font-medium text-[color:var(--color-ink)] focus:outline-none"
      >
        {LANGUAGES.map((lang) => (
          <option key={lang.code} value={lang.code} className="bg-[color:var(--color-surface-raised)] text-[color:var(--color-ink)]">
            {lang.nativeLabel}
          </option>
        ))}
      </select>
      <svg
        viewBox="0 0 10 6"
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 right-2.5 h-1.5 w-2.5 -translate-y-1/2 text-[color:var(--color-ink-faint)]"
      >
        <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </label>
  )
}
