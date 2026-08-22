import { Camera, CircleX, UploadCloud } from 'lucide-react'
import { useRef, useState } from 'react'
import { Card } from '../ui/Card'

const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp']
const MAX_BYTES = 10 * 1024 * 1024

interface UploadCardProps {
  onFileAccepted: (file: File) => void
}

/**
 * The empty/idle state. Validation here is real client-side logic (type +
 * size, matching the documented `POST /crop-images` limits) — not a
 * simulation of a backend error. Two inputs feed the same acceptance path:
 * a plain file picker (with drag & drop) and a camera-capture input that
 * requests the rear camera on supported mobile/tablet browsers.
 */
export function UploadCard({ onFileAccepted }: UploadCardProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function validateAndAccept(file: File | undefined) {
    if (!file) return
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setError('Unsupported file type. Please upload a JPG, PNG, or WebP image.')
      return
    }
    if (file.size > MAX_BYTES) {
      setError('That image is over 10 MB. Please upload a smaller file.')
      return
    }
    setError(null)
    onFileAccepted(file)
  }

  return (
    <Card className="p-5" id="crop-diagnosis-upload">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_TYPES.join(',')}
        className="sr-only"
        onChange={(event) => validateAndAccept(event.target.files?.[0])}
      />
      {/* `capture="environment"` requests the rear camera directly on supported
          mobile/tablet browsers; desktop browsers fall back to a normal file picker. */}
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        onChange={(event) => validateAndAccept(event.target.files?.[0])}
      />

      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault()
          setDragActive(true)
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragActive(false)
          validateAndAccept(event.dataTransfer.files?.[0])
        }}
        className={`flex h-56 w-full flex-col items-center justify-center gap-3 rounded-xl border border-dashed text-center transition-colors ${
          dragActive
            ? 'border-lime-400/60 bg-lime-500/[0.06]'
            : 'border-white/[0.14] bg-white/[0.02] hover:border-lime-500/40 hover:bg-lime-500/[0.04]'
        }`}
      >
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-lime-500/10">
          <UploadCloud size={22} strokeWidth={1.5} className="text-lime-400" />
        </span>
        <span className="text-sm font-medium text-[color:var(--color-ink)]">Drag & drop or click to upload</span>
        <span className="text-xs text-[color:var(--color-ink-faint)]">JPG, PNG, or WebP · up to 10 MB</span>
      </button>

      <div className="mt-3 flex items-center gap-3">
        <div className="h-px flex-1 bg-white/[0.06]" />
        <span className="text-[11px] text-[color:var(--color-ink-faint)]">or</span>
        <div className="h-px flex-1 bg-white/[0.06]" />
      </div>

      <button
        type="button"
        onClick={() => cameraInputRef.current?.click()}
        className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-lime-500/10 py-2.5 text-sm font-medium text-lime-300 transition-colors hover:bg-lime-500/15"
        style={{ boxShadow: '0 0 0 1px color-mix(in oklab, var(--color-lime-500) 30%, transparent)' }}
      >
        <Camera size={16} strokeWidth={1.5} />
        Take Photo
      </button>

      {error && (
        <p className="mt-3 flex items-start gap-1.5 text-xs text-[color:var(--color-status-critical)]">
          <CircleX size={13} strokeWidth={1.5} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}
    </Card>
  )
}
