import type { OptimizeRequest, OptimizeResponse } from '../types'

const ACTION_STYLE = {
  charge: 'text-teal-600 dark:text-teal-400',
  discharge: 'text-emerald-600 dark:text-emerald-400',
  idle: 'text-slate-400',
}

export function PlanTable({ request, response }: { request: OptimizeRequest; response: OptimizeResponse }) {
  const hours = [...request.hours].sort((a, b) => a.hour - b.hour)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-right font-mono text-xs">
        <thead className="text-slate-500">
          <tr className="border-b border-slate-200 dark:border-slate-800">
            {['Hour', 'Demand', 'Solar fcst', 'Tariff', 'Grid', 'Solar used', 'Action', 'Battery kWh', 'Energy after', 'Cost'].map((h) => (
              <th key={h} className="px-2 py-2 font-semibold whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {response.hourly_plan.map((p, i) => (
            <tr key={p.hour} className="border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/40">
              <td className="px-2 py-1">{p.hour}</td>
              <td className="px-2 py-1">{hours[i].demand_kwh}</td>
              <td className="px-2 py-1">{hours[i].solar_kwh}</td>
              <td className="px-2 py-1">{hours[i].tariff_bdt_per_kwh}</td>
              <td className="px-2 py-1">{p.grid_kwh.toFixed(2)}</td>
              <td className="px-2 py-1">{p.solar_used_kwh.toFixed(2)}</td>
              <td className={`px-2 py-1 ${ACTION_STYLE[p.battery_action]}`}>{p.battery_action}</td>
              <td className="px-2 py-1">{p.battery_kwh.toFixed(2)}</td>
              <td className="px-2 py-1">{p.battery_energy_after_kwh.toFixed(2)}</td>
              <td className="px-2 py-1">{(p.grid_kwh * hours[i].tariff_bdt_per_kwh).toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
