import type { Directive, OptimizeRequest, OptimizeResponse } from './types'

const TOL = 0.01 // judge tolerance

export interface HourConstraints {
  effSolar: number[]
  floor: number[]
  canCharge: boolean[]
  canDischarge: boolean[]
  gridCap: number[]
}

/** Mirror of app/optimizer.py build_constraints: what the directives mean for each hour. */
export function buildConstraints(req: OptimizeRequest, directives: Directive[]): HourConstraints {
  const hours = [...req.hours].sort((a, b) => a.hour - b.hour)
  const c: HourConstraints = {
    effSolar: hours.map((h) => h.solar_kwh),
    floor: hours.map(() => req.battery.minimum_energy_kwh),
    canCharge: hours.map(() => true),
    canDischarge: hours.map(() => true),
    gridCap: hours.map(() => Infinity),
  }
  for (const d of directives) {
    const a = d.structured_adjustment
    if (!d.applies || !a) continue
    for (const h of a.hours) {
      switch (d.directive_type) {
        case 'solar_reduction':
          c.effSolar[h] *= a.factor ?? 1
          break
        case 'minimum_battery_reserve':
          c.floor[h] = Math.max(c.floor[h], a.minimum_energy_kwh ?? 0)
          break
        case 'no_charge_window':
          c.canCharge[h] = false
          break
        case 'no_discharge_window':
          c.canDischarge[h] = false
          break
        case 'max_grid_window':
          c.gridCap[h] = Math.min(c.gridCap[h], a.max_grid_kwh ?? Infinity)
          break
      }
    }
  }
  return c
}

export interface Check {
  label: string
  ok: boolean
  detail?: string
}

/** Independent in-browser replay of the returned plan, the same checks the judge runs. */
export function replay(req: OptimizeRequest, res: OptimizeResponse): Check[] {
  const c = buildConstraints(req, res.directive_interpretation)
  const b = req.battery
  const hours = [...req.hours].sort((a, b) => a.hour - b.hour)
  const plan = res.hourly_plan
  const fails: Record<string, string[]> = {
    balance: [], battery: [], rates: [], solar: [], windows: [], reserve: [], grid: [], sign: [],
  }
  let energy = b.initial_energy_kwh
  plan.forEach((p, i) => {
    const h = p.hour
    const charge = p.battery_action === 'charge' ? p.battery_kwh : 0
    const discharge = p.battery_action === 'discharge' ? p.battery_kwh : 0
    if ([p.grid_kwh, p.solar_used_kwh, p.battery_kwh, p.battery_energy_after_kwh].some((v) => v < -TOL))
      fails.sign.push(`h${h}`)
    if (p.battery_action === 'idle' && Math.abs(p.battery_kwh) > TOL) fails.sign.push(`h${h}`)
    if (Math.abs(p.grid_kwh + p.solar_used_kwh + discharge - hours[i].demand_kwh - charge) > TOL)
      fails.balance.push(`h${h}`)
    energy += charge - discharge
    if (Math.abs(energy - p.battery_energy_after_kwh) > TOL || energy > b.capacity_kwh + TOL || energy < b.minimum_energy_kwh - TOL)
      fails.battery.push(`h${h}`)
    if (charge > b.max_charge_kwh_per_hour + TOL || discharge > b.max_discharge_kwh_per_hour + TOL)
      fails.rates.push(`h${h}`)
    if (p.solar_used_kwh > c.effSolar[h] + TOL) fails.solar.push(`h${h}`)
    if ((charge > TOL && !c.canCharge[h]) || (discharge > TOL && !c.canDischarge[h])) fails.windows.push(`h${h}`)
    if (energy < c.floor[h] - TOL) fails.reserve.push(`h${h}`)
    if (p.grid_kwh > c.gridCap[h] + TOL) fails.grid.push(`h${h}`)
  })

  const totalGrid = plan.reduce((s, p) => s + p.grid_kwh, 0)
  const totalCost = plan.reduce((s, p, i) => s + p.grid_kwh * hours[i].tariff_bdt_per_kwh, 0)
  const peak = Math.max(...plan.map((p) => p.grid_kwh))
  const totalsOk =
    Math.abs(totalGrid - res.total_grid_kwh) <= TOL &&
    Math.abs(totalCost - res.total_cost_bdt) <= TOL &&
    Math.abs(peak - res.peak_grid_kwh) <= TOL

  const mk = (label: string, key: string): Check => ({
    label,
    ok: fails[key].length === 0,
    detail: fails[key].length ? `violated at ${fails[key].join(', ')}` : undefined,
  })
  return [
    { label: '24 hours in order', ok: plan.length === 24 && plan.every((p, i) => p.hour === i) },
    mk('Energy balance every hour', 'balance'),
    mk('Battery state & bounds', 'battery'),
    mk('Charge / discharge rate limits', 'rates'),
    mk('Solar ≤ effective solar', 'solar'),
    mk('No-charge / no-discharge windows', 'windows'),
    mk('Reserve floors respected', 'reserve'),
    mk('Grid caps respected', 'grid'),
    mk('Non-negative, consistent actions', 'sign'),
    {
      label: 'End-of-day battery = initial',
      ok: Math.abs(energy - b.initial_energy_kwh) <= TOL,
      detail: `${energy.toFixed(2)} vs ${b.initial_energy_kwh}`,
    },
    { label: 'Totals match recomputed plan', ok: totalsOk },
  ]
}
