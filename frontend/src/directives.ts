import type { Directive, DirectiveType } from './types'

export const DIRECTIVE_META: Record<DirectiveType, { label: string; color: string; chip: string }> = {
  solar_reduction: { label: 'Solar reduction', color: '#f59e0b', chip: 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200' },
  minimum_battery_reserve: { label: 'Battery reserve', color: '#8b5cf6', chip: 'bg-violet-100 text-violet-900 dark:bg-violet-900/40 dark:text-violet-200' },
  no_charge_window: { label: 'No-charge window', color: '#0ea5e9', chip: 'bg-sky-100 text-sky-900 dark:bg-sky-900/40 dark:text-sky-200' },
  no_discharge_window: { label: 'No-discharge window', color: '#f43f5e', chip: 'bg-rose-100 text-rose-900 dark:bg-rose-900/40 dark:text-rose-200' },
  max_grid_window: { label: 'Grid import cap', color: '#ef4444', chip: 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200' },
  no_op: { label: 'No-op (ignored)', color: '#94a3b8', chip: 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300' },
}

/** Human description of the numeric part of a directive. */
export function describeValue(d: Directive): string | null {
  const a = d.structured_adjustment
  if (!a) return null
  switch (d.directive_type) {
    case 'solar_reduction':
      return `${Math.round((a.factor ?? 0) * 1000) / 10}% of forecast solar remains`
    case 'minimum_battery_reserve':
      return `keep ≥ ${a.minimum_energy_kwh} kWh in the battery`
    case 'max_grid_window':
      return `grid import ≤ ${a.max_grid_kwh} kWh/h`
    case 'no_charge_window':
      return 'battery may not charge'
    case 'no_discharge_window':
      return 'battery may not discharge'
    default:
      return null
  }
}

/** Collapse [22,23,0,1] style hour lists into contiguous [start, end] runs for chart shading. */
export function hourRuns(hours: number[]): [number, number][] {
  const sorted = [...hours].sort((a, b) => a - b)
  const runs: [number, number][] = []
  for (const h of sorted) {
    const last = runs[runs.length - 1]
    if (last && h === last[1] + 1) last[1] = h
    else runs.push([h, h])
  }
  return runs
}

export const fmtHour = (h: number) => `${String(h).padStart(2, '0')}:00`
