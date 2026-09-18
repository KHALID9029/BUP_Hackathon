export type DirectiveType =
  | 'solar_reduction'
  | 'minimum_battery_reserve'
  | 'no_charge_window'
  | 'no_discharge_window'
  | 'max_grid_window'
  | 'no_op'

export interface HourInput {
  hour: number
  demand_kwh: number
  solar_kwh: number
  tariff_bdt_per_kwh: number
}

export interface Battery {
  capacity_kwh: number
  initial_energy_kwh: number
  minimum_energy_kwh: number
  max_charge_kwh_per_hour: number
  max_discharge_kwh_per_hour: number
}

export interface OptimizeRequest {
  scenario_id: string
  operator_notes: string[]
  hours: HourInput[]
  battery: Battery
}

export interface Adjustment {
  hours: number[]
  factor?: number
  minimum_energy_kwh?: number
  max_grid_kwh?: number
}

export interface Directive {
  note_index: number
  applies: boolean
  directive_type: DirectiveType
  structured_adjustment: Adjustment | null
  explanation: string
}

export interface HourPlan {
  hour: number
  grid_kwh: number
  solar_used_kwh: number
  battery_action: 'charge' | 'discharge' | 'idle'
  battery_kwh: number
  battery_energy_after_kwh: number
}

export interface OptimizeResponse {
  scenario_id: string
  directive_interpretation: Directive[]
  hourly_plan: HourPlan[]
  total_grid_kwh: number
  total_cost_bdt: number
  peak_grid_kwh: number
  plan_summary: string
}

export interface SampleCase {
  id: string
  label: string
  rationale: string
  input: OptimizeRequest
  expected: Directive[]
  expectedCost: number
}
