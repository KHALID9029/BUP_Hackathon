import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { DIRECTIVE_META, hourRuns } from '../directives'
import type { Directive, OptimizeRequest, OptimizeResponse } from '../types'
import type { HourConstraints } from '../validate'

interface Props {
  request: OptimizeRequest
  response: OptimizeResponse
  constraints: HourConstraints
}

export const HOUR_TICKS = Array.from({ length: 24 }, (_, i) => i)
export const AXIS = { stroke: '#94a3b8', fontSize: 11 }

/** Shaded bands behind the chart for every applied directive's hours. */
export function DirectiveBands({ directives, types }: { directives: Directive[]; types?: string[] }) {
  return directives
    .filter((d) => d.applies && d.structured_adjustment && (!types || types.includes(d.directive_type)))
    .flatMap((d) =>
      hourRuns(d.structured_adjustment!.hours).map(([s, e]) => (
        <ReferenceArea
          key={`${d.note_index}-${s}`}
          yAxisId="kwh"
          x1={s - 0.5}
          x2={e + 0.5}
          fill={DIRECTIVE_META[d.directive_type].color}
          fillOpacity={0.13}
          stroke={DIRECTIVE_META[d.directive_type].color}
          strokeOpacity={0.35}
          strokeDasharray="3 3"
          ifOverflow="extendDomain"
        />
      )),
    )
}

const DEMAND_COLOR = window.matchMedia?.('(prefers-color-scheme: dark)').matches ? '#e2e8f0' : '#0f172a'

const round = (v: number) => Math.round(v * 100) / 100

export function EnergyChart({ request, response, constraints }: Props) {
  const hours = [...request.hours].sort((a, b) => a.hour - b.hour)
  const data = response.hourly_plan.map((p, i) => ({
    hour: p.hour,
    grid: p.grid_kwh,
    solar: p.solar_used_kwh,
    discharge: p.battery_action === 'discharge' ? p.battery_kwh : 0,
    charge: p.battery_action === 'charge' ? -p.battery_kwh : 0,
    demand: hours[i].demand_kwh,
    effSolar: round(constraints.effSolar[i]),
    tariff: hours[i].tariff_bdt_per_kwh,
    gridCap: Number.isFinite(constraints.gridCap[i]) ? constraints.gridCap[i] : null,
  }))
  const hasCap = data.some((d) => d.gridCap !== null)

  return (
    <ResponsiveContainer width="100%" height={360}>
      <ComposedChart data={data} stackOffset="sign" margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.25} />
        {DirectiveBands({ directives: response.directive_interpretation })}
        <XAxis dataKey="hour" type="number" domain={[-0.5, 23.5]} ticks={HOUR_TICKS} {...AXIS} />
        <YAxis yAxisId="kwh" {...AXIS} label={{ value: 'kWh', angle: -90, position: 'insideLeft', fill: '#94a3b8', fontSize: 11 }} />
        <YAxis yAxisId="bdt" orientation="right" {...AXIS} label={{ value: 'BDT/kWh', angle: 90, position: 'insideRight', fill: '#94a3b8', fontSize: 11 }} />
        <Tooltip
          contentStyle={{ fontSize: 12, borderRadius: 8, color: "#0f172a" }}
          labelFormatter={(h) => `Hour ${h}`}
          formatter={(v, name) => [typeof v === 'number' ? Math.abs(v).toFixed(2) : v, name]}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar yAxisId="kwh" dataKey="grid" name="Grid import" stackId="s" fill="#6366f1" />
        <Bar yAxisId="kwh" dataKey="solar" name="Solar used" stackId="s" fill="#f59e0b" />
        <Bar yAxisId="kwh" dataKey="discharge" name="Battery discharge" stackId="s" fill="#10b981" />
        <Bar yAxisId="kwh" dataKey="charge" name="Battery charge (−)" stackId="s" fill="#14b8a6" fillOpacity={0.55} />
        <Line yAxisId="kwh" dataKey="demand" name="Demand" type="monotone" stroke={DEMAND_COLOR} strokeWidth={2} dot={false} />
        <Line yAxisId="kwh" dataKey="effSolar" name="Usable solar" type="monotone" stroke="#d97706" strokeDasharray="4 3" dot={false} />
        {hasCap && (
          <Line yAxisId="kwh" dataKey="gridCap" name="Grid cap" type="step" stroke="#ef4444" strokeWidth={2} dot={false} connectNulls={false} />
        )}
        <Line yAxisId="bdt" dataKey="tariff" name="Tariff" type="step" stroke="#e11d48" strokeWidth={1.5} strokeOpacity={0.7} dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
