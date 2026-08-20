import type { components } from '../api/types.gen'

export type ScoreBand = components['schemas']['ScoreBand']
export type RiskLevel = components['schemas']['RiskLevel']

export type Status = 'good' | 'warning' | 'serious' | 'critical'

const SCORE_BAND_STATUS: Record<ScoreBand, Status> = {
  excellent: 'good',
  good: 'good',
  moderate: 'warning',
  poor: 'serious',
  critical: 'critical',
}

const RISK_LEVEL_STATUS: Record<RiskLevel, Status> = {
  low: 'good',
  moderate: 'warning',
  high: 'serious',
  severe: 'critical',
}

export const STATUS_COLOR: Record<Status, string> = {
  good: 'var(--color-status-good)',
  warning: 'var(--color-status-warning)',
  serious: 'var(--color-status-serious)',
  critical: 'var(--color-status-critical)',
}

export const STATUS_TEXT_CLASS: Record<Status, string> = {
  good: 'text-[color:var(--color-status-good)]',
  warning: 'text-[color:var(--color-status-warning)]',
  serious: 'text-[color:var(--color-status-serious)]',
  critical: 'text-[color:var(--color-status-critical)]',
}

export const STATUS_BG_CLASS: Record<Status, string> = {
  good: 'bg-[color:var(--color-status-good)]/10',
  warning: 'bg-[color:var(--color-status-warning)]/10',
  serious: 'bg-[color:var(--color-status-serious)]/10',
  critical: 'bg-[color:var(--color-status-critical)]/10',
}

export function statusFromScoreBand(band: ScoreBand): Status {
  return SCORE_BAND_STATUS[band]
}

export function statusFromRiskLevel(level: RiskLevel): Status {
  return RISK_LEVEL_STATUS[level]
}

export function titleCase(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
