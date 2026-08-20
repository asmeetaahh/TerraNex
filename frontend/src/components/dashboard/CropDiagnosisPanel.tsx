import { AlertTriangle, Camera, CheckCircle2, ImageIcon, Loader2, Sparkles, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { ApiError, apiClient } from '../../api/client'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { AIModeBadge } from '../ui/Badge'
import { shortDate } from '../../lib/format'
import { STATUS_TEXT_CLASS, type Status } from '../../lib/status'

type CropImage = components['schemas']['CropImage']
type Severity = components['schemas']['Severity']

const SEVERITY_STATUS: Record<Severity, Status> = {
  none: 'good',
  mild: 'warning',
  moderate: 'serious',
  severe: 'critical',
}

export function CropDiagnosisPanel({
  farmId,
  recentImages,
}: {
  farmId: string
  recentImages: CropImage[]
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [status, setStatus] = useState<'idle' | 'uploading' | 'notice'>('idle')
  const [notice, setNotice] = useState<string | null>(null)

  function handleFile(file: File | undefined) {
    if (!file) return
    setPreviewUrl(URL.createObjectURL(file))
    setStatus('uploading')
    setNotice(null)

    const form = new FormData()
    form.append('file', file)

    apiClient
      .upload<CropImage>(`/farms/${farmId}/crop-images?analyze=true`, form)
      .then(() => {
        setStatus('idle')
      })
      .catch((error: unknown) => {
        setStatus('notice')
        if (error instanceof ApiError && error.code === 'NOT_IMPLEMENTED') {
          setNotice('Crop image upload isn’t wired up on the backend yet — this is what the flow will look like once it is.')
        } else {
          setNotice('Upload failed. Check your connection and try again.')
        }
      })
  }

  const latestImage = recentImages[0] ?? null

  return (
    <Card className="p-5" id="crop-diagnosis">
      <h2 className="mb-1 flex items-center gap-2 text-base font-semibold text-[color:var(--color-ink)]">
        <Camera size={16} strokeWidth={1.5} className="text-lime-400" />
        AI Crop Diagnosis
      </h2>
      <p className="mb-4 text-xs text-[color:var(--color-ink-faint)]">
        Photograph a leaf or plant showing symptoms for an AI-assisted read.
      </p>

      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        className="sr-only"
        onChange={(event) => handleFile(event.target.files?.[0])}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="flex h-32 w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-white/[0.14] bg-white/[0.02] text-center transition-colors hover:border-lime-500/40 hover:bg-lime-500/[0.04]"
      >
        {previewUrl ? (
          <img src={previewUrl} alt="Selected crop" className="h-full w-full rounded-xl object-cover" />
        ) : status === 'uploading' ? (
          <>
            <Loader2 size={20} className="animate-spin text-lime-400" strokeWidth={1.5} />
            <span className="text-xs text-[color:var(--color-ink-faint)]">Uploading…</span>
          </>
        ) : (
          <>
            <Upload size={20} strokeWidth={1.5} className="text-lime-400" />
            <span className="text-xs text-[color:var(--color-ink-muted)]">Drag & drop or click to upload</span>
            <span className="text-[11px] text-[color:var(--color-ink-faint)]">JPG, PNG up to 10MB</span>
          </>
        )}
      </button>

      {notice && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-[color:var(--color-mode-simulated)]">
          <AlertTriangle size={13} strokeWidth={1.5} className="mt-0.5 shrink-0" />
          {notice}
        </p>
      )}

      {recentImages.length > 0 && (
        <div className="mt-5">
          <p className="mb-2 text-xs font-medium text-[color:var(--color-ink-muted)]">Recent Diagnoses</p>
          <div className="grid grid-cols-3 gap-2">
            {recentImages.slice(0, 3).map((image) => (
              <div key={image.id} className="overflow-hidden rounded-lg border border-white/[0.06] bg-white/[0.02]">
                <div className="flex h-16 w-full items-center justify-center bg-white/[0.02] text-[color:var(--color-ink-faint)]">
                  {image.url ? (
                    <img src={image.url} alt={image.note ?? 'Crop photo'} className="h-full w-full object-cover" />
                  ) : (
                    <ImageIcon size={18} strokeWidth={1.5} />
                  )}
                </div>
                <p className="truncate px-1.5 py-1 text-[10px] text-[color:var(--color-ink-faint)]">
                  {shortDate(image.uploaded_at)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      <DiagnosisResult image={latestImage} />
    </Card>
  )
}

function DiagnosisResult({ image }: { image: CropImage | null }) {
  if (!image || !image.analysis) {
    return (
      <div className="mt-5 flex h-24 items-center justify-center rounded-xl border border-white/[0.06] bg-white/[0.02] text-center text-xs text-[color:var(--color-ink-faint)]">
        No diagnosis yet. Upload a photo to see a result here.
      </div>
    )
  }

  const { analysis } = image

  if (!analysis.is_plant_material) {
    return (
      <div className="mt-5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5 text-sm text-[color:var(--color-ink-muted)]">
        This image doesn’t appear to show plant material — try a closer photo of the affected leaf.
      </div>
    )
  }

  const status = SEVERITY_STATUS[analysis.severity]

  return (
    <div className="mt-5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-[color:var(--color-ink-muted)]">Diagnosis Result</p>
        {image.ai_mode && <AIModeBadge mode={image.ai_mode} />}
      </div>
      <p className="mt-1.5 text-sm font-semibold text-[color:var(--color-ink)]">{analysis.condition_label}</p>

      <div className="mt-2 flex items-center justify-between text-xs">
        <span className="text-[color:var(--color-ink-faint)]">Confidence</span>
        <span className="font-medium text-lime-400">{Math.round(analysis.confidence * 100)}%</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <div className="h-full rounded-full bg-lime-400" style={{ width: `${Math.round(analysis.confidence * 100)}%` }} />
      </div>

      <p className={`mt-2 flex items-center gap-1.5 text-xs font-medium capitalize ${STATUS_TEXT_CLASS[status]}`}>
        <span className="h-1.5 w-1.5 rounded-full bg-current" />
        {analysis.severity} severity
      </p>

      {analysis.symptoms_observed && analysis.symptoms_observed.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium text-[color:var(--color-ink-muted)]">TerraNex Observed</p>
          <ul className="mt-1.5 space-y-1">
            {analysis.symptoms_observed.slice(0, 3).map((symptom) => (
              <li key={symptom} className="flex items-start gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
                <CheckCircle2 size={12} strokeWidth={1.5} className="mt-0.5 shrink-0 text-lime-400" />
                {symptom}
              </li>
            ))}
          </ul>
        </div>
      )}

      {analysis.immediate_actions && analysis.immediate_actions.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium text-[color:var(--color-ink-muted)]">Recommended Next Steps</p>
          <ul className="mt-1.5 space-y-1">
            {analysis.immediate_actions.slice(0, 3).map((step) => (
              <li key={step} className="flex items-start gap-1.5 text-xs text-[color:var(--color-ink-muted)]">
                <Sparkles size={12} strokeWidth={1.5} className="mt-0.5 shrink-0 text-lime-400" />
                {step}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-[color:var(--color-ink-faint)]">{analysis.disclaimer}</p>
    </div>
  )
}
