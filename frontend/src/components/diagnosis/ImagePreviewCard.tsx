import { Loader2, RotateCcw, ScanLine } from 'lucide-react'
import { Card } from '../ui/Card'
import { formatBytes } from '../../lib/format'

interface ImagePreviewCardProps {
  file: File
  previewUrl: string
  analyzing: boolean
  onAnalyze: () => void
  onReset: () => void
}

export function ImagePreviewCard({ file, previewUrl, analyzing, onAnalyze, onReset }: ImagePreviewCardProps) {
  return (
    <Card className="p-5">
      <div className="overflow-hidden rounded-xl border border-white/[0.06]">
        <img src={previewUrl} alt="Selected crop" className="h-64 w-full object-cover" />
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-[color:var(--color-ink)]">{file.name}</p>
          <p className="text-xs text-[color:var(--color-ink-faint)]">
            {file.type.replace('image/', '').toUpperCase()} · {formatBytes(file.size)}
          </p>
        </div>
        <button
          type="button"
          onClick={onReset}
          disabled={analyzing}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-white/[0.08] px-3 py-1.5 text-xs font-medium text-[color:var(--color-ink-muted)] transition-colors hover:bg-white/[0.04] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RotateCcw size={13} strokeWidth={1.5} />
          Change photo
        </button>
      </div>

      <button
        type="button"
        onClick={onAnalyze}
        disabled={analyzing}
        className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-lime-500/10 py-2.5 text-sm font-medium text-lime-300 transition-colors hover:bg-lime-500/15 disabled:cursor-not-allowed"
        style={!analyzing ? { boxShadow: '0 0 0 1px color-mix(in oklab, var(--color-lime-500) 30%, transparent)' } : undefined}
      >
        {analyzing ? (
          <>
            <Loader2 size={16} strokeWidth={1.5} className="animate-spin" />
            Analyzing photo…
          </>
        ) : (
          <>
            <ScanLine size={16} strokeWidth={1.5} />
            Analyze crop
          </>
        )}
      </button>
    </Card>
  )
}
