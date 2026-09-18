import {
  Area, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { OptimizeRequest, OptimizeResponse } from '../types'
import type { HourConstraints } from '../validate'
import { AXIS, DirectiveBands, HOUR_TICKS } from './EnergyChart'

interface Props {
  request: OptimizeRequest
  response: OptimizeResponse
  constraints: HourConstraints
}

export function BatteryChart({ request, response, constraints }: Props) {
  const b = request.battery
  const data = response.hourly_plan.map((p, i) => ({
    hour: p.hour,
    energy: p.battery_energy_after_kwh,
    floor: constraints.floor[i],
  }))

  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.25} />
        {DirectiveBands({
          directives: response.directive_interpretation,
          types: ['minimum_battery_reserve', 'no_charge_window', 'no_discharge_window'],
        })}
        <XAxis dataKey="hour" type="number" domain={[-0.5, 23.5]} ticks={HOUR_TICKS} {...AXIS} />
        <YAxis yAxisId="kwh" domain={[0, Math.ceil(b.capacity_kwh * 1.05)]} {...AXIS} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, color: "#0f172a" }} labelFormatter={(h) => `End of hour ${h}`}
          formatter={(v, name) => [typeof v === 'number' ? v.toFixed(2) + ' kWh' : v, name]} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <ReferenceLine yAxisId="kwh" y={b.capacity_kwh} stroke="#64748b" strokeDasharray="6 4"
          label={{ value: `capacity ${b.capacity_kwh}`, position: 'insideTopLeft', fill: '#64748b', fontSize: 11 }} />
        <ReferenceLine yAxisId="kwh" y={b.initial_energy_kwh} stroke="#10b981" strokeDasharray="2 4"
          label={{ value: `initial ${b.initial_energy_kwh}`, position: 'insideBottomRight', fill: '#10b981', fontSize: 11 }} />
        <Area yAxisId="kwh" dataKey="energy" name="Battery energy after hour" type="linear" stroke="#10b981" strokeWidth={2} fill="#10b981" fillOpacity={0.18} />
        <Line yAxisId="kwh" dataKey="floor" name="Minimum floor (incl. reserve)" type="step" stroke="#8b5cf6" strokeWidth={2} dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
