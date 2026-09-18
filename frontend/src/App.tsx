import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { BatteryChart } from './components/BatteryChart'
import { EnergyChart } from './components/EnergyChart'
import { InterpretationCards } from './components/InterpretationCards'
import { PlanTable } from './components/PlanTable'
import { ScenarioPanel } from './components/ScenarioPanel'
import { SAMPLES } from './samples'
import type { OptimizeRequest, OptimizeResponse } from './types'
import { buildConstraints, replay } from './validate'

type Health = 'checking' | 'ok' | 'down'

interface RunResult {
  request: OptimizeRequest
  response: OptimizeResponse
  ms: number
  sampleId: string | null // set when the request was an unmodified public sample
}

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v))

function Card({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <h2 className="text-sm font-semibold">{title}</h2>
      {subtitle && <p className="mb-3 text-xs text-slate-500">{subtitle}</p>}
      {children}
    </section>
  )
}

function Stat({ label, value, unit, hint }: { label: string; value: string; unit: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
      <p className="text-xs font-medium tracking-wide text-slate-500 uppercase">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">
        {value} <span className="text-sm font-normal text-slate-500">{unit}</span>
      </p>
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

const PIPELINE = ['LLM interpreter', 'Guardrails', 'LP optimizer (HiGHS)', 'Replay validator']

export default function App() {
  const [health, setHealth] = useState<Health>('checking')
  const [sampleId, setSampleId] = useState(SAMPLES[0].id)
  const [request, setRequest] = useState<OptimizeRequest>(() => clone(SAMPLES[0].input))
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<{ status: number; body: unknown } | null>(null)
  const [result, setResult] = useState<RunResult | null>(null)

  useEffect(() => {
    const check = () =>
      fetch('/health')
        .then((r) => r.json())
        .then((j) => setHealth(j.status === 'ok' ? 'ok' : 'down'))
        .catch(() => setHealth('down'))
    check()
    const t = setInterval(check, 15000)
    return () => clearInterval(t)
  }, [])

  const loadSample = (id: string) => {
    setSampleId(id)
    const s = SAMPLES.find((x) => x.id === id)
    if (s) setRequest(clone(s.input))
  }

  const editRequest = (r: OptimizeRequest) => {
    setRequest(r)
    setSampleId('custom')
  }

  const run = async () => {
    setRunning(true)
    setError(null)
    const sent = clone(request)
    const t0 = performance.now()
    try {
      const r = await fetch('/optimize-energy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sent),
      })
      const body = await r.json().catch(() => null)
      if (!r.ok) {
        setError({ status: r.status, body })
      } else {
        setResult({ request: sent, response: body, ms: performance.now() - t0, sampleId: sampleId === 'custom' ? null : sampleId })
      }
    } catch (e) {
      setError({ status: 0, body: String(e) })
    } finally {
      setRunning(false)
    }
  }

  const derived = useMemo(() => {
    if (!result) return null
    const constraints = buildConstraints(result.request, result.response.directive_interpretation)
    const checks = replay(result.request, result.response)
    const sample = SAMPLES.find((s) => s.id === result.sampleId)
    return { constraints, checks, sample }
  }, [result])

  const allOk = derived?.checks.every((c) => c.ok)
  const res = result?.response

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6">
      <header className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">⚡ GridWise LLM</h1>
        </div>
        <ol className="flex flex-wrap items-center gap-1.5 text-xs">
          {PIPELINE.map((step, i) => (
            <li key={step} className="flex items-center gap-1.5">
              <span className="rounded-full border border-slate-300 px-2.5 py-1 dark:border-slate-700">{step}</span>
              {i < PIPELINE.length - 1 && <span className="text-slate-400">→</span>}
            </li>
          ))}
        </ol>
        <span className="ml-auto flex items-center gap-2 text-xs text-slate-500">
          <span className={`h-2.5 w-2.5 rounded-full ${health === 'ok' ? 'bg-emerald-500' : health === 'down' ? 'bg-rose-500' : 'bg-amber-400'}`} />
          API {health === 'ok' ? 'healthy' : health === 'down' ? 'unreachable' : 'checking…'}
        </span>
      </header>

      <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
        <aside className="lg:sticky lg:top-6 lg:self-start">
          <ScenarioPanel request={request} sampleId={sampleId} onSample={loadSample} onChange={editRequest} onRun={run} running={running} />
        </aside>

        <main className="min-w-0 space-y-6">
          {error && (
            <div className="rounded-xl border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-900 dark:bg-rose-950/50 dark:text-rose-200">
              <p className="font-semibold">Request failed{error.status ? ` — HTTP ${error.status}` : ''}</p>
              <pre className="mt-2 overflow-x-auto text-xs whitespace-pre-wrap">{JSON.stringify(error.body, null, 2)}</pre>
            </div>
          )}

          {!res && !error && (
            <div className="flex min-h-[420px] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
              <p className="text-lg font-medium">{running ? 'Running the pipeline…' : 'Pick a scenario and run it'}</p>
              <p className="mt-1 max-w-md text-sm text-slate-500">
                The LLM turns each operator note into a directive, the guardrails validate it, a linear program finds the
                cheapest valid schedule, and this page replays that schedule independently to check it.
              </p>
            </div>
          )}

          {res && derived && result && (
            <div className={`space-y-6 transition-opacity ${running ? 'opacity-50' : ''}`}>
              <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <Stat
                  label="Total cost"
                  value={res.total_cost_bdt.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                  unit="BDT"
                  hint={
                    derived.sample
                      ? `reference ${derived.sample.expectedCost.toLocaleString()} · ratio ${(derived.sample.expectedCost / res.total_cost_bdt).toFixed(4)}`
                      : undefined
                  }
                />
                <Stat label="Grid energy" value={res.total_grid_kwh.toLocaleString(undefined, { maximumFractionDigits: 2 })} unit="kWh" />
                <Stat label="Peak grid" value={res.peak_grid_kwh.toLocaleString(undefined, { maximumFractionDigits: 2 })} unit="kWh/h" />
                <Stat label="Response time" value={(result.ms / 1000).toFixed(2)} unit="s" hint={`scenario ${res.scenario_id}`} />
              </div>

              <Card title="Directive interpretation" subtitle="What the LLM extracted from each note, after guardrail validation">
                <InterpretationCards notes={result.request.operator_notes} directives={res.directive_interpretation} expected={derived.sample?.expected} />
              </Card>

              <Card title="Hourly energy schedule" subtitle="Supply stacked above zero, battery charging below; shaded bands mark the hours each directive covers">
                <EnergyChart request={result.request} response={res} constraints={derived.constraints} />
              </Card>

              <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
                <Card title="Battery state of charge" subtitle="Energy at the end of each hour against the minimum floor and capacity">
                  <BatteryChart request={result.request} response={res} constraints={derived.constraints} />
                </Card>
                <Card title={allOk ? '✓ Plan independently verified' : '✗ Plan has violations'} subtitle="Replayed hour by hour in your browser (±0.01 tolerance)">
                  <ul className="space-y-1.5 text-sm">
                    {derived.checks.map((c) => (
                      <li key={c.label} className="flex gap-2">
                        <span className={c.ok ? 'text-emerald-500' : 'text-rose-500'}>{c.ok ? '✓' : '✗'}</span>
                        <span>
                          {c.label}
                          {!c.ok && c.detail && <span className="block text-xs text-rose-500">{c.detail}</span>}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Card>
              </div>

              <Card title="Plan summary">
                <p className="text-sm leading-relaxed text-slate-600 dark:text-slate-300">{res.plan_summary}</p>
              </Card>

              <details className="rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
                <summary className="cursor-pointer p-5 text-sm font-semibold">Hourly plan table</summary>
                <div className="px-5 pb-5">
                  <PlanTable request={result.request} response={res} />
                </div>
              </details>

              <details className="rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900/60">
                <summary className="cursor-pointer p-5 text-sm font-semibold">Raw JSON response</summary>
                <pre className="max-h-[480px] overflow-auto px-5 pb-5 font-mono text-xs">{JSON.stringify(res, null, 2)}</pre>
              </details>
            </div>
          )}
        </main>
      </div>

      <footer className="mt-10 text-center text-xs text-slate-400">
        BUP CSE Fest 2026 · GridWise LLM · API: <code>GET /health</code>, <code>POST /optimize-energy</code>
      </footer>
    </div>
  )
}
