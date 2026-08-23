import { CircleX } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { analyzeCropImage, uploadCropImage, type CropImage } from '../api/cropImages'
import { useFarmDashboard } from '../hooks/useFarmDashboard'
import { UploadCard } from '../components/diagnosis/UploadCard'
import { ImagePreviewCard } from '../components/diagnosis/ImagePreviewCard'
import { DiagnosisResultCard } from '../components/diagnosis/DiagnosisResultCard'

type Phase = 'upload' | 'selected' | 'analyzing' | 'result'

/**
 * Turns a failure into something a grower can act on. Branches on `code`, never
 * on message text — the codes are the contract, the messages are not.
 */
function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'IMAGE_TOO_LARGE':
        return 'The server rejected that image as too large. Try a smaller photo.'
      case 'UNSUPPORTED_MEDIA_TYPE':
        return 'That file type isn’t supported. Upload a JPG, PNG, or WebP image.'
      case 'AI_UNAVAILABLE':
        return 'The diagnosis service is unavailable right now. Try again in a moment.'
      case 'ANALYSIS_IN_PROGRESS':
        return 'This photo is already being analysed. Give it a few seconds and try again.'
      case 'FARM_NOT_FOUND':
        return 'That farm is no longer available. Reload the page and pick another.'
      default:
        return error.message
    }
  }
  if (error instanceof Error) return error.message
  return 'Something went wrong running the diagnosis.'
}

export function DiagnosisPage() {
  const { mode, selectedFarmId } = useFarmDashboard()

  const [phase, setPhase] = useState<Phase>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [result, setResult] = useState<CropImage | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Bumped whenever the user picks a new photo, retries, or resets, so a reply
  // from an abandoned upload/analyze pair can never overwrite the current view.
  const runRef = useRef(0)

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  function handleFileAccepted(selected: File) {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    runRef.current += 1
    setFile(selected)
    setPreviewUrl(URL.createObjectURL(selected))
    setResult(null)
    setError(null)
    setPhase('selected')
  }

  /**
   * The real flow: upload the file, then diagnose the stored image. Failure
   * returns to `selected` with the photo still on screen, so the same button
   * retries the same file — no sample result is ever substituted.
   */
  async function handleAnalyze() {
    if (!file) return

    if (!selectedFarmId) {
      setError(
        mode === 'loading'
          ? 'Still loading your farms — try again in a moment.'
          : 'Crop photos are stored against a farm, and none is selected. Add a farm first.',
      )
      return
    }

    const run = (runRef.current += 1)
    setError(null)
    setPhase('analyzing')

    try {
      const uploaded = await uploadCropImage(selectedFarmId, file)
      const diagnosed = await analyzeCropImage(uploaded.id)

      if (run !== runRef.current) return

      if (!diagnosed.analysis) {
        setError(diagnosed.analysis_error ?? 'The diagnosis came back empty. Try again.')
        setPhase('selected')
        return
      }

      setResult(diagnosed)
      setPhase('result')
    } catch (failure: unknown) {
      if (run !== runRef.current) return
      setError(messageFor(failure))
      setPhase('selected')
    }
  }

  function handleReset() {
    runRef.current += 1
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setFile(null)
    setPreviewUrl(null)
    setResult(null)
    setError(null)
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

      {error && (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-xl border border-[color:var(--color-status-critical)]/25 bg-[color:var(--color-status-critical)]/[0.06] px-4 py-3"
        >
          <CircleX size={16} strokeWidth={1.5} className="mt-0.5 shrink-0 text-[color:var(--color-status-critical)]" />
          <p className="text-sm text-[color:var(--color-ink-muted)]">
            <span className="font-medium text-[color:var(--color-ink)]">Diagnosis failed.</span> {error}
          </p>
        </div>
      )}

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
