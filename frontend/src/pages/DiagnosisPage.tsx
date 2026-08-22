import { Info } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { components } from '../api/types.gen'
import { previewCropImage } from '../data/previewFixtures'
import { UploadCard } from '../components/diagnosis/UploadCard'
import { ImagePreviewCard } from '../components/diagnosis/ImagePreviewCard'
import { DiagnosisResultCard } from '../components/diagnosis/DiagnosisResultCard'

type CropImage = components['schemas']['CropImage']

type Phase = 'upload' | 'selected' | 'analyzing' | 'result'

const ANALYZE_DELAY_MS = 1400

export function DiagnosisPage() {
  const [phase, setPhase] = useState<Phase>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [result, setResult] = useState<CropImage | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [previewUrl])

  function handleFileAccepted(selected: File) {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setFile(selected)
    setPreviewUrl(URL.createObjectURL(selected))
    setResult(null)
    setPhase('selected')
  }

  function handleAnalyze() {
    if (!file) return
    setPhase('analyzing')
    timeoutRef.current = setTimeout(() => {
      setResult({
        ...previewCropImage,
        uploaded_at: new Date().toISOString(),
        content_type: file.type,
        size_bytes: file.size,
        note: file.name,
      })
      setPhase('result')
    }, ANALYZE_DELAY_MS)
  }

  function handleReset() {
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setFile(null)
    setPreviewUrl(null)
    setResult(null)
    setPhase('upload')
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 pb-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)] sm:text-[26px]">Crop Diagnosis</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-faint)]">
          Upload a photo of a leaf or crop to identify possible diseases and get actionable guidance.
        </p>
      </header>

      <div className="flex items-start gap-3 rounded-xl border border-[color:var(--color-mode-simulated)]/25 bg-[color:var(--color-mode-simulated)]/[0.06] px-4 py-3">
        <Info size={16} strokeWidth={1.5} className="mt-0.5 shrink-0 text-[color:var(--color-mode-simulated)]" />
        <p className="text-sm text-[color:var(--color-ink-muted)]">
          <span className="font-medium text-[color:var(--color-ink)]">Preview flow.</span> Image upload and analysis
          aren't wired up to the backend yet — this shows the full diagnosis experience using sample results so the
          flow can be reviewed before integration.
        </p>
      </div>

      {phase === 'upload' && <UploadCard onFileAccepted={handleFileAccepted} />}

      {(phase === 'selected' || phase === 'analyzing') && file && previewUrl && (
        <ImagePreviewCard
          file={file}
          previewUrl={previewUrl}
          analyzing={phase === 'analyzing'}
          onAnalyze={handleAnalyze}
          onReset={handleReset}
        />
      )}

      {phase === 'result' && result && previewUrl && (
        <DiagnosisResultCard image={result} previewUrl={previewUrl} onReset={handleReset} />
      )}
    </div>
  )
}
