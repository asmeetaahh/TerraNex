import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Bot, Loader2, Mic, MicOff, Send, Sparkles, Square, Volume2 } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { AIModeBadge } from '../ui/Badge'
import { STATUS_COLOR, statusFromScoreBand } from '../../lib/status'
import {
  buildAskTerraNexResponse,
  buildAskTerraNexResponseForCategory,
  type AskTerraNexData,
  type AskTerraNexResponse,
  type SuggestedQuestionId,
} from '../../lib/askTerraNex'
import { DEFAULT_LANGUAGE, languageConfig, type LanguageCode } from '../../lib/i18n'
import { getStrings } from '../../lib/askTerraNexStrings'
import { LanguageSelector } from './LanguageSelector'

type SpeechRecognitionLike = {
  continuous: boolean
  interimResults: boolean
  lang: string
  onstart: (() => void) | null
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}

type SpeechWindow = typeof window & {
  SpeechRecognition?: new () => SpeechRecognitionLike
  webkitSpeechRecognition?: new () => SpeechRecognitionLike
}

/** idle → listening (mic) → processing (fake "thinking" beat, keeps this feeling like an
 * assistant rather than an instant lookup) → response → optionally speaking (read-aloud).
 * `error` covers both a real recognition failure and an unsupported browser. */
type Phase = 'idle' | 'listening' | 'processing' | 'response' | 'speaking' | 'error'

const SUGGESTED_QUESTION_IDS: SuggestedQuestionId[] = ['water', 'heat', 'cropHealth', 'soil']

const PROCESSING_DELAY_MS = 450

function getSpeechRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === 'undefined') return null
  const w = window as SpeechWindow
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

function speechSynthesisSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined'
}

export function AskTerraNex({ data, aiMode }: { data: AskTerraNexData; aiMode: components['schemas']['AIMode'] }) {
  const [language, setLanguage] = useState<LanguageCode>(DEFAULT_LANGUAGE)
  const [question, setQuestion] = useState('')
  const [response, setResponse] = useState<AskTerraNexResponse | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [micError, setMicError] = useState<string | null>(null)

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const processingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const strings = getStrings(language)
  const speechLang = languageConfig(language).speechLang
  const speechSupported = getSpeechRecognitionCtor() !== null
  const ttsSupported = speechSynthesisSupported()

  useEffect(() => {
    return () => {
      if (processingTimeoutRef.current) clearTimeout(processingTimeoutRef.current)
      recognitionRef.current?.stop()
      if (ttsSupported) window.speechSynthesis.cancel()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function ask(text: string, category?: SuggestedQuestionId) {
    const trimmed = text.trim()
    if (!trimmed) return

    if (ttsSupported) window.speechSynthesis.cancel()
    setQuestion(trimmed)
    setPhase('processing')

    if (processingTimeoutRef.current) clearTimeout(processingTimeoutRef.current)
    processingTimeoutRef.current = setTimeout(() => {
      const result = category ? buildAskTerraNexResponseForCategory(category, data) : buildAskTerraNexResponse(trimmed, data)
      setResponse(result)
      setPhase('response')
    }, PROCESSING_DELAY_MS)
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    ask(question)
  }

  function toggleListening() {
    const Ctor = getSpeechRecognitionCtor()
    if (!Ctor) return

    if (phase === 'listening' || recognitionRef.current) {
      recognitionRef.current?.stop()
      recognitionRef.current = null
      setPhase('idle')
      return
    }

    setMicError(null)

    const recognition = new Ctor()
    recognition.continuous = false
    recognition.interimResults = true
    recognition.lang = speechLang
    recognition.onstart = () => setPhase('listening')
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results)
        .map((result) => result[0]?.transcript ?? '')
        .join(' ')
      setQuestion(transcript)
    }
    recognition.onerror = (event) => {
      recognitionRef.current = null
      setPhase('error')
      setMicError(
        event?.error === 'not-allowed' || event?.error === 'service-not-allowed'
          ? strings.micBlocked
          : event?.error === 'no-speech'
            ? strings.micNoSpeech
            : strings.micGeneric,
      )
    }
    recognition.onend = () => {
      recognitionRef.current = null
      setPhase((current) => (current === 'listening' ? 'idle' : current))
    }

    recognitionRef.current = recognition
    try {
      recognition.start()
    } catch {
      recognitionRef.current = null
      setPhase('error')
      setMicError(strings.micGeneric)
    }
  }

  function toggleSpeaking() {
    if (!ttsSupported || !response) return

    if (phase === 'speaking') {
      window.speechSynthesis.cancel()
      setPhase('response')
      return
    }

    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(response.text)
    utterance.lang = speechLang
    utterance.onend = () => setPhase((current) => (current === 'speaking' ? 'response' : current))
    utterance.onerror = () => setPhase((current) => (current === 'speaking' ? 'response' : current))
    setPhase('speaking')
    window.speechSynthesis.speak(utterance)
  }

  const listening = phase === 'listening'
  const processing = phase === 'processing'
  const speaking = phase === 'speaking'

  return (
    <Card glow className="relative overflow-hidden p-6 sm:p-10">
      <div
        className="pointer-events-none absolute -top-24 -right-24 h-72 w-72 rounded-full opacity-40 blur-3xl"
        style={{ background: 'radial-gradient(circle, color-mix(in oklab, var(--color-lime-400) 35%, transparent), transparent 70%)' }}
      />

      <div className="relative">
        <div className="flex justify-center sm:justify-end">
          <LanguageSelector value={language} onChange={setLanguage} />
        </div>

        <div className="mt-2 flex flex-col items-center gap-3 text-center">
          <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-lime-500/10 text-lime-400 shadow-[inset_0_0_0_1px_rgba(159,227,92,0.18)]">
            <Bot size={26} strokeWidth={1.5} />
          </span>
          <div>
            <h2 className="text-2xl font-semibold text-[color:var(--color-ink)] sm:text-[28px]">Ask TerraNex</h2>
            <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">{strings.subtitle}</p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="mx-auto mt-6 max-w-2xl">
          <div className="flex flex-col gap-2 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-3 sm:flex-row sm:items-end">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={strings.placeholder}
              rows={2}
              dir="auto"
              className="min-w-0 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-[color:var(--color-ink)] placeholder:text-[color:var(--color-ink-faint)] focus:outline-none"
            />
            <div className="flex shrink-0 items-center justify-end gap-2">
              <button
                type="button"
                onClick={toggleListening}
                disabled={!speechSupported || processing}
                title={speechSupported ? strings.micTooltip : strings.micUnsupported}
                aria-label={strings.micTooltip}
                className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full border transition-colors ${
                  listening
                    ? 'border-lime-400/40 bg-lime-500/15 text-lime-300'
                    : 'border-white/[0.08] text-[color:var(--color-ink-muted)] hover:bg-white/[0.04] hover:text-[color:var(--color-ink)]'
                } disabled:cursor-not-allowed disabled:opacity-40`}
              >
                {listening ? <MicOff size={17} strokeWidth={1.5} /> : <Mic size={17} strokeWidth={1.5} />}
              </button>
              <button
                type="submit"
                disabled={!question.trim() || processing}
                className="flex items-center gap-1.5 rounded-full bg-lime-500 px-5 py-2.5 text-sm font-semibold text-black shadow-[0_0_24px_-8px_rgba(143,224,60,0.7)] transition-all duration-150 hover:bg-lime-400 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none disabled:active:scale-100"
              >
                {processing ? <Loader2 size={14} strokeWidth={2} className="animate-spin" /> : <Send size={14} strokeWidth={2} />}
                {strings.askButton}
              </button>
            </div>
          </div>

          {listening && (
            <p className="mt-1.5 flex items-center justify-center gap-1.5 px-1 text-[11px] text-lime-300">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-lime-400" />
              {strings.listening}
            </p>
          )}
          {processing && (
            <p className="mt-1.5 flex items-center justify-center gap-1.5 px-1 text-[11px] text-[color:var(--color-ink-faint)]">
              <Loader2 size={11} strokeWidth={2} className="animate-spin" />
              {strings.processing}
            </p>
          )}
          {phase === 'error' && micError && (
            <p className="mt-1.5 flex items-center justify-center gap-1.5 px-1 text-center text-[11px] text-[color:var(--color-status-warning)]">
              {micError}
            </p>
          )}

          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {SUGGESTED_QUESTION_IDS.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => ask(strings.questions[id], id)}
                disabled={processing}
                className="rounded-full border border-white/[0.08] px-3 py-1.5 text-xs text-[color:var(--color-ink-muted)] transition-colors hover:border-lime-400/30 hover:bg-lime-500/10 hover:text-lime-300 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {strings.questions[id]}
              </button>
            ))}
          </div>
        </form>

        {response && (phase === 'response' || phase === 'speaking') && (
          <div className="mx-auto mt-6 max-w-2xl rounded-2xl border border-lime-400/15 bg-lime-500/[0.04] p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Sparkles size={13} strokeWidth={2} className="text-lime-400" />
                <span className="text-xs font-semibold tracking-wide text-[color:var(--color-ink)] uppercase">TerraNex</span>
                <AIModeBadge mode={aiMode} />
              </div>
              <button
                type="button"
                onClick={toggleSpeaking}
                disabled={!ttsSupported}
                title={ttsSupported ? strings.speakerTooltip : strings.speakerUnsupported}
                aria-label={ttsSupported ? strings.speakerTooltip : strings.speakerUnsupported}
                className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  speaking
                    ? 'border-lime-400/40 bg-lime-500/15 text-lime-300'
                    : 'border-white/[0.08] text-[color:var(--color-ink-muted)] hover:bg-white/[0.04] hover:text-[color:var(--color-ink)]'
                } disabled:cursor-not-allowed disabled:opacity-30`}
              >
                {speaking ? <Square size={12} strokeWidth={2} /> : <Volume2 size={13} strokeWidth={1.5} />}
                {speaking ? strings.stopSpeaking : null}
              </button>
            </div>

            <p className="mt-2 text-[10px] font-medium tracking-wide text-lime-400/80 uppercase">{strings.previewAi}</p>
            <p className="mt-1 text-sm leading-relaxed text-[color:var(--color-ink-muted)]">{response.text}</p>
            {language !== 'en' && (
              <p className="mt-2 text-[11px] text-[color:var(--color-ink-faint)] italic">{strings.responseLanguageNote}</p>
            )}
            {speaking && (
              <p className="mt-2 flex items-center gap-1.5 text-[11px] text-lime-300">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-lime-400" />
                {strings.speaking}
              </p>
            )}

            {response.signals.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {response.signals.map((signal) => {
                  const color = STATUS_COLOR[statusFromScoreBand(signal.band)]
                  return (
                    <span
                      key={signal.key}
                      className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium"
                      style={{ backgroundColor: `color-mix(in oklab, ${color} 12%, transparent)`, color }}
                    >
                      {signal.label} · {Math.round(signal.score)}/100
                    </span>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  )
}
