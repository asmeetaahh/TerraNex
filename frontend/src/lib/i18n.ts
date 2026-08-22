/**
 * Centralized language configuration for the Ask TerraNex voice/language layer.
 * Frontend-only — this drives interface strings and the `lang` tag passed to the
 * browser's SpeechRecognition/SpeechSynthesis APIs. Add a new language by adding
 * one entry here plus a matching block in `lib/askTerraNexStrings.ts`.
 */

export type LanguageCode = 'en' | 'hi' | 'pt' | 'ru' | 'zh' | 'ar'

export interface LanguageConfig {
  code: LanguageCode
  /** Name in its own language, for the selector. */
  nativeLabel: string
  /** Name in English, for the tooltip/aria-label. */
  englishLabel: string
  /** BCP-47 tag passed to SpeechRecognition.lang / SpeechSynthesisUtterance.lang. */
  speechLang: string
  rtl?: boolean
}

export const LANGUAGES: LanguageConfig[] = [
  { code: 'en', nativeLabel: 'English', englishLabel: 'English', speechLang: 'en-US' },
  { code: 'hi', nativeLabel: 'हिन्दी', englishLabel: 'Hindi', speechLang: 'hi-IN' },
  { code: 'pt', nativeLabel: 'Português', englishLabel: 'Portuguese', speechLang: 'pt-BR' },
  { code: 'ru', nativeLabel: 'Русский', englishLabel: 'Russian', speechLang: 'ru-RU' },
  { code: 'zh', nativeLabel: '中文', englishLabel: 'Mandarin Chinese', speechLang: 'zh-CN' },
  { code: 'ar', nativeLabel: 'العربية', englishLabel: 'Arabic', speechLang: 'ar-SA', rtl: true },
]

export const DEFAULT_LANGUAGE: LanguageCode = 'en'

export function languageConfig(code: LanguageCode): LanguageConfig {
  return LANGUAGES.find((l) => l.code === code) ?? LANGUAGES[0]
}
