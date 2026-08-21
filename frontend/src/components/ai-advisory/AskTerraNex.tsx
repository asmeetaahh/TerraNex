import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Bot, Mic, MicOff, Send, Sparkles } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { AIModeBadge } from '../ui/Badge'
import { STATUS_COLOR, statusFromScoreBand } from '../../lib/status'
import { buildAskTerraNexResponse, type AskTerraNexData, type AskTerraNexResponse } from '../../lib/askTerraNex'

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

const SUGGESTED_QUESTIONS = [
  'Why is my water risk severe?',
  'What should I do about the heat stress?',
  'Why is my crop health low?',
  'How can I improve my soil?',
]

function getSpeechRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === 'undefined') return null
  const w = window as SpeechWindow
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export function AskTerraNex({ data, aiMode }: { data: AskTerraNexData; aiMode: components['schemas']['AIMode'] }) {
  const [question, setQuestion] = useState('')
  const [response, setResponse] = useState<AskTerraNexResponse | null>(null)
  const [listening, setListening] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)

  const speechSupported = getSpeechRecognitionCtor() !== null

  function ask(text: string) {
    const trimmed = text.trim()
    if (!trimmed) return
    setQuestion(trimmed)
    setResponse(buildAskTerraNexResponse(trimmed, data))
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    ask(question)
  }

  function toggleListening() {
    const Ctor = getSpeechRecognitionCtor()
    if (!Ctor) return

    if (listening || recognitionRef.current) {
      recognitionRef.current?.stop()
      recognitionRef.current = null
      setListening(false)
      return
    }

    setMicError(null)

    const recognition = new Ctor()
    recognition.continuous = false
    recognition.interimResults = true
    recognition.lang = 'en-US'
    recognition.onstart = () => setListening(true)
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results)
        .map((result) => result[0]?.transcript ?? '')
        .join(' ')
      setQuestion(transcript)
    }
    recognition.onerror = (event) => {
      setListening(false)
      recognitionRef.current = null
      setMicError(
        event?.error === 'not-allowed' || event?.error === 'service-not-allowed'
          ? 'Microphone access was blocked — allow it in your browser to use voice input.'
          : event?.error === 'no-speech'
            ? "Didn't catch that — try again."
            : 'Voice input is unavailable right now.',
      )
    }
    recognition.onend = () => {
      setListening(false)
      recognitionRef.current = null
    }

    recognitionRef.current = recognition
    try {
      recognition.start()
    } catch {
      recognitionRef.current = null
      setListening(false)
      setMicError('Voice input is unavailable in this browser.')
    }
  }

  return (
    <Card glow className="relative overflow-hidden p-6 sm:p-10">
      <div
        className="pointer-events-none absolute -top-24 -right-24 h-72 w-72 rounded-full opacity-40 blur-3xl"
        style={{ background: 'radial-gradient(circle, color-mix(in oklab, var(--color-lime-400) 35%, transparent), transparent 70%)' }}
      />

      <div className="relative">
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-lime-500/10 text-lime-400 shadow-[inset_0_0_0_1px_rgba(159,227,92,0.18)]">
            <Bot size={26} strokeWidth={1.5} />
          </span>
          <div>
            <h2 className="text-2xl font-semibold text-[color:var(--color-ink)] sm:text-[28px]">Ask TerraNex</h2>
            <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
              Tell TerraNex what's concerning you about your farm.
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="mx-auto mt-6 max-w-2xl">
          <div className="flex flex-col gap-2 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-3 sm:flex-row sm:items-end">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. My crops are looking weak and the leaves are turning yellow..."
              rows={2}
              className="min-w-0 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-[color:var(--color-ink)] placeholder:text-[color:var(--color-ink-faint)] focus:outline-none"
            />
            <div className="flex shrink-0 items-center justify-end gap-2">
              <button
                type="button"
                onClick={toggleListening}
                disabled={!speechSupported}
                title={speechSupported ? 'Voice input (preview)' : 'Voice input preview — not available in this browser'}
                aria-label="Ask by voice (preview)"
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
                disabled={!question.trim()}
                className="flex items-center gap-1.5 rounded-full bg-lime-500 px-5 py-2.5 text-sm font-semibold text-black shadow-[0_0_24px_-8px_rgba(143,224,60,0.7)] transition-colors hover:bg-lime-400 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
              >
                <Send size={14} strokeWidth={2} />
                Ask TerraNex
              </button>
            </div>
          </div>
          {listening && (
            <p className="mt-1.5 flex items-center justify-center gap-1.5 px-1 text-[11px] text-lime-300">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-lime-400" />
              Listening (voice preview) — speak now
            </p>
          )}
          {!listening && micError && (
            <p className="mt-1.5 flex items-center justify-center gap-1.5 px-1 text-center text-[11px] text-[color:var(--color-status-warning)]">
              {micError}
            </p>
          )}

          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {SUGGESTED_QUESTIONS.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => ask(q)}
                className="rounded-full border border-white/[0.08] px-3 py-1.5 text-xs text-[color:var(--color-ink-muted)] transition-colors hover:border-lime-400/30 hover:bg-lime-500/10 hover:text-lime-300"
              >
                {q}
              </button>
            ))}
          </div>
        </form>

        {response && (
          <div className="mx-auto mt-6 max-w-2xl rounded-2xl border border-lime-400/15 bg-lime-500/[0.04] p-4 sm:p-5">
            <div className="flex flex-wrap items-center gap-2">
              <Sparkles size={13} strokeWidth={2} className="text-lime-400" />
              <span className="text-xs font-semibold tracking-wide text-[color:var(--color-ink)] uppercase">TerraNex</span>
              <AIModeBadge mode={aiMode} />
            </div>

            <p className="mt-2 text-sm leading-relaxed text-[color:var(--color-ink-muted)]">{response.text}</p>

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
