import { useState } from 'react'
import { SAMPLES } from '../samples'
import type { Battery, OptimizeRequest } from '../types'

interface Props {
  request: OptimizeRequest
  sampleId: string
  onSample: (id: string) => void
  onChange: (r: OptimizeRequest) => void
  onRun: () => void
  running: boolean
}

const BATTERY_FIELDS: [keyof Battery, string][] = [
  ['capacity_kwh', 'Capacity'],
  ['initial_energy_kwh', 'Initial energy'],
  ['minimum_energy_kwh', 'Minimum energy'],
  ['max_charge_kwh_per_hour', 'Max charge / h'],
  ['max_discharge_kwh_per_hour', 'Max discharge / h'],
]

const input =
  'w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:border-slate-700 dark:bg-slate-900'

export function ScenarioPanel({ request, sampleId, onSample, onChange, onRun, running }: Props) {
  const [hoursText, setHoursText] = useState<string | null>(null)
  const [hoursError, setHoursError] = useState<string | null>(null)
  const sample = SAMPLES.find((s) => s.id === sampleId)

  const setNote = (i: number, v: string) =>
    onChange({ ...request, operator_notes: request.operator_notes.map((n, j) => (j === i ? v : n)) })

  return (
    <section className="space-y-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <div>
        <label className="mb-1 block text-xs font-semibold tracking-wide text-slate-500 uppercase">Load a public sample</label>
        <select className={input} value={sampleId} onChange={(e) => { setHoursText(null); onSample(e.target.value) }}>
          {SAMPLES.map((s) => (
            <option key={s.id} value={s.id}>
              {s.id} · {s.label}
            </option>
          ))}
          <option value="custom">Custom scenario (edited)</option>
        </select>
        {sample && <p className="mt-2 text-xs leading-relaxed text-slate-500">{sample.rationale}</p>}
      </div>

      <div>
        <label className="mb-1 block text-xs font-semibold tracking-wide text-slate-500 uppercase">Scenario ID</label>
        <input className={input} value={request.scenario_id} onChange={(e) => onChange({ ...request, scenario_id: e.target.value })} />
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Operator notes ({request.operator_notes.length}/3)
          </label>
          <button
            type="button"
            disabled={request.operator_notes.length >= 3}
            onClick={() => onChange({ ...request, operator_notes: [...request.operator_notes, ''] })}
            className="text-xs font-medium text-indigo-600 hover:underline disabled:opacity-40 dark:text-indigo-400"
          >
            + Add note
          </button>
        </div>
        <div className="space-y-2">
          {request.operator_notes.map((n, i) => (
            <div key={i} className="flex gap-2">
              <span className="mt-2 w-5 shrink-0 text-xs font-mono text-slate-400">#{i}</span>
              <textarea rows={3} className={input} value={n} onChange={(e) => setNote(i, e.target.value)}
                placeholder="e.g. The substation limits grid import to 120 kWh from 6 PM to 9 PM." />
              <button
                type="button"
                title="Remove note"
                disabled={request.operator_notes.length <= 1}
                onClick={() => onChange({ ...request, operator_notes: request.operator_notes.filter((_, j) => j !== i) })}
                className="self-start px-1 text-lg leading-none text-slate-400 hover:text-rose-500 disabled:opacity-30"
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-slate-500">Try your own paraphrases — the LLM interprets any wording.</p>
      </div>

      <div>
        <label className="mb-1 block text-xs font-semibold tracking-wide text-slate-500 uppercase">Battery (kWh)</label>
        <div className="grid grid-cols-2 gap-2">
          {BATTERY_FIELDS.map(([k, label]) => (
            <label key={k} className="text-xs text-slate-500">
              {label}
              <input
                type="number"
                className={input + ' mt-0.5'}
                value={request.battery[k]}
                onChange={(e) => onChange({ ...request, battery: { ...request.battery, [k]: Number(e.target.value) } })}
              />
            </label>
          ))}
        </div>
      </div>

      <details className="text-sm">
        <summary className="cursor-pointer text-xs font-semibold tracking-wide text-slate-500 uppercase">
          Hourly demand / solar / tariff (JSON)
        </summary>
        <textarea
          rows={10}
          spellCheck={false}
          className={input + ' mt-2 font-mono text-xs'}
          value={hoursText ?? JSON.stringify(request.hours, null, 0).replace(/},{/g, '},\n{')}
          onChange={(e) => {
            setHoursText(e.target.value)
            try {
              const parsed = JSON.parse(e.target.value)
              if (!Array.isArray(parsed)) throw new Error('must be an array')
              setHoursError(null)
              onChange({ ...request, hours: parsed })
            } catch (err) {
              setHoursError((err as Error).message)
            }
          }}
        />
        {hoursError && <p className="mt-1 text-xs text-rose-600">Invalid JSON: {hoursError}</p>}
      </details>

      <button
        onClick={onRun}
        disabled={running}
        className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-60"
      >
        {running ? 'Interpreting & optimizing…' : 'Run optimization'}
      </button>
    </section>
  )
}
