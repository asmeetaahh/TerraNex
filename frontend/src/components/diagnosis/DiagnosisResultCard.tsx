import { CheckCircle2, RotateCcw, ShieldAlert, ShieldCheck, Sparkles, Stethoscope } from 'lucide-react'
import type { components } from '../../api/types.gen'
import { Card } from '../ui/Card'
import { AIModeBadge } from '../ui/Badge'
import { STATUS_BG_CLASS, STATUS_TEXT_CLASS, type Status } from '../../lib/status'

type CropImage = components['schemas']['CropImage']
type Severity = components['schemas']['Severity']

const SEVERITY_STATUS: Record<Severity, Status> = {
  none: 'good',
  mild: 'warning',
  moderate: 'serious',
  severe: 'critical',
}

interface DiagnosisResultCardProps {
  image: CropImage
  previewUrl: string
  onReset: () => void
}

export function DiagnosisResultCard({ image, previewUrl, onReset }: DiagnosisResultCardProps) {
  const { analysis } = image

  if (!analysis) return null

  if (!analysis.is_plant_material) {
    return (
      <Card className="flex flex-col items-center gap-3 px-6 py-14 text-center">
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-[color:var(--color-status-serious)]/10">
          <ShieldAlert size={22} strokeWidth={1.5} className="text-[color:var(--color-status-serious)]" />
        </span>
        <h2 className="text-base font-semibold text-[color:var(--color-ink)]">No plant material detected</h2>
        <p className="max-w-sm text-sm text-[color:var(--color-ink-faint)]">
          This image doesn't appear to show a leaf or plant. Try a closer, well-lit photo of the affected area.
        </p>
        <button
          type="button"
          onClick={onReset}
          className="mt-1 flex items-center gap-1.5 rounded-lg border border-white/[0.08] px-4 py-2 text-sm font-medium text-[color:var(--color-ink-muted)] transition-colors hover:bg-white/[0.04]"
        >
          <RotateCcw size={14} strokeWidth={1.5} />
          Try another photo
        </button>
      </Card>
    )
  }

  const status = SEVERITY_STATUS[analysis.severity]

  return (
    <Card className="overflow-hidden p-0">
      <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr]">
        <img src={previewUrl} alt="Diagnosed crop" className="h-48 w-full object-cover lg:h-full" />

        <div className="p-5">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-[color:var(--color-ink-muted)] uppercase">
                <Stethoscope size={13} strokeWidth={1.5} className="text-lime-400" />
                Diagnosis Result
              </p>
              <h2 className="mt-1 text-xl font-semibold text-[color:var(--color-ink)]">{analysis.condition_label}</h2>
              {analysis.crop_identified && (
                <p className="mt-0.5 text-xs text-[color:var(--color-ink-faint)] capitalize">
                  Crop identified: {analysis.crop_identified}
                </p>
              )}
            </div>
            {image.ai_mode && <AIModeBadge mode={image.ai_mode} />}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-xs text-[color:var(--color-ink-faint)]">Confidence</span>
              <div className="h-1.5 w-24 overflow-hidden rounded-full bg-white/[0.06]">
                <div className="h-full rounded-full bg-lime-400" style={{ width: `${Math.round(analysis.confidence * 100)}%` }} />
              </div>
              <span className="text-xs font-semibold text-lime-400">{Math.round(analysis.confidence * 100)}%</span>
            </div>
            <span className={`flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STATUS_BG_CLASS[status]} ${STATUS_TEXT_CLASS[status]}`}>
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {analysis.severity} severity
            </span>
            {analysis.affected_area_pct != null && (
              <span className="text-xs text-[color:var(--color-ink-faint)]">{analysis.affected_area_pct}% of area affected</span>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 border-t border-white/[0.06] p-5 sm:grid-cols-2">
        {analysis.symptoms_observed && analysis.symptoms_observed.length > 0 && (
          <div>
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-[color:var(--color-ink-muted)] uppercase">Observed Symptoms</h3>
            <ul className="space-y-1.5">
              {analysis.symptoms_observed.map((symptom) => (
                <li key={symptom} className="flex items-start gap-1.5 text-sm text-[color:var(--color-ink-muted)]">
                  <CheckCircle2 size={13} strokeWidth={1.5} className="mt-0.5 shrink-0 text-lime-400" />
                  {symptom}
                </li>
              ))}
            </ul>
          </div>
        )}

        {analysis.differential_diagnoses && analysis.differential_diagnoses.length > 0 && (
          <div>
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-[color:var(--color-ink-muted)] uppercase">Possible Causes</h3>
            <ul className="space-y-2">
              {analysis.differential_diagnoses.map((item) => (
                <li key={item.condition} className="text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-[color:var(--color-ink)]">{item.condition_label}</span>
                    <span className="text-xs text-[color:var(--color-ink-faint)]">{Math.round(item.likelihood * 100)}% likely</span>
                  </div>
                  {item.distinguishing_features && (
                    <p className="mt-0.5 text-xs text-[color:var(--color-ink-faint)]">{item.distinguishing_features}</p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {analysis.immediate_actions && analysis.immediate_actions.length > 0 && (
          <div>
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-[color:var(--color-ink-muted)] uppercase">Recommended Actions</h3>
            <ul className="space-y-1.5">
              {analysis.immediate_actions.map((step) => (
                <li key={step} className="flex items-start gap-1.5 text-sm text-[color:var(--color-ink-muted)]">
                  <Sparkles size={13} strokeWidth={1.5} className="mt-0.5 shrink-0 text-lime-400" />
                  {step}
                </li>
              ))}
            </ul>
          </div>
        )}

        {analysis.prevention && analysis.prevention.length > 0 && (
          <div>
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-[color:var(--color-ink-muted)] uppercase">Prevention</h3>
            <ul className="space-y-1.5">
              {analysis.prevention.map((tip) => (
                <li key={tip} className="flex items-start gap-1.5 text-sm text-[color:var(--color-ink-muted)]">
                  <ShieldCheck size={13} strokeWidth={1.5} className="mt-0.5 shrink-0 text-lime-400" />
                  {tip}
                </li>
              ))}
            </ul>
          </div>
        )}

        {analysis.treatment_options && analysis.treatment_options.length > 0 && (
          <div className="sm:col-span-2">
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-[color:var(--color-ink-muted)] uppercase">Treatment Options</h3>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {analysis.treatment_options.map((option) => (
                <div key={option.name} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-[color:var(--color-ink)]">{option.name}</p>
                    <span className="shrink-0 rounded-full border border-white/[0.08] px-1.5 py-0.5 text-[10px] text-[color:var(--color-ink-faint)] capitalize">
                      {option.approach}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-[color:var(--color-ink-muted)]">{option.description}</p>
                  {option.timing && <p className="mt-1 text-[11px] text-[color:var(--color-ink-faint)]">Timing: {option.timing}</p>}
                  {option.precautions && (
                    <p className="mt-0.5 text-[11px] text-[color:var(--color-status-warning)]">{option.precautions}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-3 border-t border-white/[0.06] bg-white/[0.02] p-5 sm:flex-row sm:items-center sm:justify-between">
        <p className="flex items-start gap-1.5 text-xs leading-relaxed text-[color:var(--color-ink-faint)]">
          <ShieldAlert size={14} strokeWidth={1.5} className="mt-0.5 shrink-0 text-[color:var(--color-status-warning)]" />
          {analysis.disclaimer}
        </p>
        <button
          type="button"
          onClick={onReset}
          className="flex shrink-0 items-center justify-center gap-1.5 rounded-lg border border-white/[0.08] px-4 py-2 text-xs font-medium text-[color:var(--color-ink-muted)] transition-colors hover:bg-white/[0.04]"
        >
          <RotateCcw size={13} strokeWidth={1.5} />
          Diagnose another photo
        </button>
      </div>
    </Card>
  )
}
