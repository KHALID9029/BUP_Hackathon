import raw from '../../Problem_doc/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json'
import type { Directive, OptimizeRequest, SampleCase } from './types'

interface RawCase {
  id: string
  label: string
  rationale: string
  input: OptimizeRequest
  expected_output: { directive_interpretation: Directive[]; total_cost_bdt: number }
}

export const SAMPLES: SampleCase[] = (raw as unknown as { cases: RawCase[] }).cases.map((c) => ({
  id: c.id,
  label: c.label,
  rationale: c.rationale,
  input: c.input,
  expected: c.expected_output.directive_interpretation,
  expectedCost: c.expected_output.total_cost_bdt,
}))

const close = (a: number | undefined, b: number | undefined) =>
  a !== undefined && b !== undefined && Math.abs(a - b) <= 0.01

/** Same matching rule as scripts/run_samples.py: type, applies, hours and numeric values; explanation ignored. */
export function matchesExpected(got: Directive, exp: Directive): boolean {
  if (got.directive_type !== exp.directive_type || got.applies !== exp.applies) return false
  const g = got.structured_adjustment
  const e = exp.structured_adjustment
  if (e === null) return g === null
  if (g === null || g.hours.join() !== e.hours.join()) return false
  return (['factor', 'minimum_energy_kwh', 'max_grid_kwh'] as const).every(
    (k) => e[k] === undefined || close(g[k], e[k]),
  )
}
