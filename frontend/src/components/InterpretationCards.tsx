import { DIRECTIVE_META, describeValue, fmtHour, hourRuns } from '../directives'
import { matchesExpected } from '../samples'
import type { Directive } from '../types'

interface Props {
  notes: string[]
  directives: Directive[]
  expected?: Directive[] // present only when an unmodified public sample was run
}

export function InterpretationCards({ notes, directives, expected }: Props) {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {directives.map((d) => {
        const meta = DIRECTIVE_META[d.directive_type]
        const value = describeValue(d)
        const exp = expected?.[d.note_index]
        const match = exp ? matchesExpected(d, exp) : null
        return (
          <article
            key={d.note_index}
            className={`flex flex-col gap-2.5 rounded-xl border bg-white p-4 shadow-sm dark:bg-slate-900/60 ${
              d.applies ? 'border-slate-200 dark:border-slate-800' : 'border-dashed border-slate-300 opacity-80 dark:border-slate-700'
            }`}
            style={{ borderLeftWidth: 4, borderLeftColor: meta.color, borderLeftStyle: 'solid' }}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-slate-400">note #{d.note_index}</span>
              <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${meta.chip}`}>{meta.label}</span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                  d.applies ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' : 'bg-slate-100 text-slate-500 dark:bg-slate-800'
                }`}
              >
                {d.applies ? 'applies' : 'ignored'}
              </span>
              {match !== null && (
                <span
                  title="Compared with the organizer's expected interpretation"
                  className={`ml-auto text-xs font-medium ${match ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600'}`}
                >
                  {match ? '✓ matches expected' : '✗ differs from expected'}
                </span>
              )}
            </div>
            <blockquote className="border-l-2 border-slate-200 pl-3 text-sm text-slate-600 italic dark:border-slate-700 dark:text-slate-400">
              “{notes[d.note_index]}”
            </blockquote>
            {d.structured_adjustment && (
              <div className="space-y-1.5">
                <div className="flex flex-wrap gap-1">
                  {hourRuns(d.structured_adjustment.hours).map(([s, e]) => (
                    <span key={s} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs dark:bg-slate-800">
                      {fmtHour(s)}–{fmtHour(e + 1 === 24 ? 0 : e + 1)}
                    </span>
                  ))}
                </div>
                {value && <p className="text-sm font-medium">{value}</p>}
                <p className="font-mono text-[11px] break-all text-slate-400">
                  {JSON.stringify(d.structured_adjustment)}
                </p>
              </div>
            )}
            <p className="mt-auto text-xs text-slate-500">{d.explanation}</p>
          </article>
        )
      })}
    </div>
  )
}
