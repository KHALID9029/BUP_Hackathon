# GridWise LLM — BUP CSE Fest 2026 Hackathon (Online Preliminary)

Consolidated reference built from the three official docs in this folder:
`Preliminary_Problem_Statement`, `Participant_Guide_&_Evaluation_Rubric`, and `Preli_Public_Sample_Cases.json`.

> **Canonical source split:** the **Problem Statement** governs challenge behavior — API schema, operator-note directives, guardrails, battery/energy rules, optimization validity. The **Participant Guide** governs everything else — deployment, submission, scoring, penalties, tie-breaks. If they ever conflict, the Problem Statement wins.

---

## 1. The 30-second version

Build **one HTTP API** that:
1. Takes a 24-hour campus energy scenario (demand, solar, grid tariff, battery specs) + 1-3 natural-language operator notes.
2. Uses an **LLM** to interpret each note into a structured directive (or `no_op` if irrelevant).
3. Validates that interpretation deterministically (guardrails).
4. Runs an **optimizer** that applies the valid directives and minimizes total grid electricity cost.
5. Returns the interpretation + a full, rule-valid 24-hour schedule.

Round window: **7:00 PM – 11:00 PM (4 hours)**, online, judged automatically.

---

## 2. Required pipeline

```
Energy Data + Operator Notes
        │
        ▼
   LLM Interpreter        ← LLM MUST be in this step (mandatory)
        │
        ▼
  Guardrail Validator      ← deterministic code, rejects/normalizes bad LLM output
        │
        ▼
   Math Optimizer          ← LP/DP/constraint solver, minimizes grid cost
        │
        ▼
   Final Validator          ← replay the schedule hour-by-hour to confirm it's valid
        │
        ▼
     API Response
```

**Core idea:** human notes are never trusted directly as math. They're converted to a fixed structured format, checked by guardrails, and only then fed to the optimizer.

**Hard requirement:** the LLM must genuinely produce the `directive_interpretation`. Using an LLM only for `plan_summary`/cosmetic text, or hard-coding phrase matching as the sole interpreter, **fails the mandatory requirement** and makes the team ineligible for the shortlist.

---

## 3. API contract

| Endpoint | Requirement |
|---|---|
| `GET /health` | HTTP 200, `{"status": "ok"}`, ready within 60s of service start |
| `POST /optimize-energy` | Accepts one scenario JSON, returns one interpretation+plan JSON |

HTTP codes: `200` success · `400` malformed/structurally invalid JSON · `422` optional (well-formed but semantically invalid) · `500` controlled internal error (no secrets/stack traces).

Per-request timeout: **30s** (anything longer = failure). Judged p95 latency: `<5s` = full points, `5-15s` = 2/3, `15-30s` = 1/3, `>30s` or timeout = 0.

### 3.1 Request schema (`POST /optimize-energy`)

```jsonc
{
  "scenario_id": "GRID-101",
  "operator_notes": [                     // array[1..3] of non-empty strings
    "Solar output will drop to about 20% from 1 PM to 3 PM.",
    "Do not charge the battery between 2 PM and 4 PM.",
    "The cafeteria menu changes tomorrow."
  ],
  "hours": [                               // array[24], hours 0..23
    {"hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7},
    // ... 22 more ...
    {"hour": 23, "demand_kwh": 200, "solar_kwh": 0, "tariff_bdt_per_kwh": 9}
  ],
  "battery": {
    "capacity_kwh": 500,
    "initial_energy_kwh": 200,
    "minimum_energy_kwh": 50,
    "max_charge_kwh_per_hour": 100,
    "max_discharge_kwh_per_hour": 100
  }
}
```

### 3.2 Response schema

```jsonc
{
  "scenario_id": "GRID-101",               // must echo request
  "directive_interpretation": [            // exactly one entry per note, in note_index order
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
      "explanation": "Solar availability is reduced during panel cleaning."
    },
    {
      "note_index": 2,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "This note does not affect today's energy schedule."
    }
  ],
  "hourly_plan": [                         // array[24], hours 0..23
    {
      "hour": 0,
      "grid_kwh": 150.0,
      "solar_used_kwh": 0.0,
      "battery_action": "charge",          // "charge" | "discharge" | "idle"
      "battery_kwh": 30.0,                 // magnitude; 0 when idle
      "battery_energy_after_kwh": 230.0
    }
    // ... 23 more ...
  ],
  "total_grid_kwh": 3120.5,
  "total_cost_bdt": 21843.75,
  "peak_grid_kwh": 210.0,
  "plan_summary": "Charged battery during off-peak hours, curtailed solar per directive..."
}
```

`total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` must exactly match values **recalculated from `hourly_plan`** — don't just report internal solver numbers.

---

## 4. Operator-note directives (the LLM interpretation layer)

Every note maps to **exactly one** of these types. Time windows are start-inclusive, end-exclusive (1 PM–3 PM → `hours: [13, 14]`).

| Directive type | Meaning | `structured_adjustment` shape | Deterministic optimizer effect |
|---|---|---|---|
| `solar_reduction` | Usable solar drops in given hours | `{"hours": [...], "factor": number}` | `effective_solar[h] = solar[h] * factor` |
| `minimum_battery_reserve` | Battery must stay ≥ level in given hours | `{"hours": [...], "minimum_energy_kwh": number}` | `battery_after[h] >= max(base_min, directive_min)` |
| `no_charge_window` | No charging allowed in given hours | `{"hours": [...]}` | charge amount = 0 |
| `no_discharge_window` | No discharging allowed in given hours | `{"hours": [...]}` | discharge amount = 0 |
| `max_grid_window` | Grid import capped in given hours | `{"hours": [...], "max_grid_kwh": number}` | `grid_kwh[h] <= max_grid_kwh` |
| `no_op` | Note is irrelevant / a distractor | `null` | none |

### Interpretation rules
- Every note → exactly one `directive_interpretation` entry, returned in `note_index` order (0..N-1), no missing/duplicate mappings.
- Only these 6 types are valid outputs — never invent a new one.
- `applies = true` for every real directive; `applies = false` **only** for `no_op`.
- `no_op` requires `structured_adjustment = null`; every other type must match its required shape exactly.
- `hours` arrays: unique integers 0–23, ascending order.
- `solar_reduction.factor` = fraction **remaining** (an 80% reduction → `factor = 0.2`), must be in `[0, 1]`.
- Reserve/grid-cap values must be finite, non-negative (reserve ≤ battery capacity).
- The LLM must **not** invent/alter base demand, tariff, or battery parameters.
- A note correctly interpreted but **not applied** to the schedule is still counted wrong — extraction and application are graded separately.
- Hidden test notes will be **paraphrased** — don't hard-code public wording. E.g. all of these mean the same `solar_reduction`:
  - "PV production will drop to about 20% between 13:00 and 15:00."
  - "Panel washing from one until three will leave roughly one-fifth of normal solar output."
  - "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window."
- **Safe failure:** if the LLM returns malformed/unsupported output, handle it in a controlled way (fallback/clamp/reject) — never crash or silently invent a directive.
- Organizer-valid scenarios are always feasible (no contradictory hard directives).

---

## 5. Optimization rules (the math layer)

**Objective:** minimize `total_cost_bdt = Σ grid_kwh[h] * tariff_bdt_per_kwh[h]` for h = 0..23, subject to every constraint below. A cheap-but-invalid schedule is worthless — validity is checked before cost.

| Rule | Formula |
|---|---|
| Battery state | charge: `E_after = E_before + battery_kwh` · discharge: `E_after = E_before - battery_kwh` · idle: `E_after = E_before`, `battery_kwh = 0` |
| Battery bounds | `minimum_energy_kwh <= E_after <= capacity_kwh` (reserve directive can raise the floor for its hours) |
| Rate limits | charge ≤ `max_charge_kwh_per_hour`; discharge ≤ `max_discharge_kwh_per_hour` |
| Solar usage | `0 <= solar_used_kwh <= effective_solar_kwh[h]` (unused solar is curtailed; no grid export) |
| Energy balance (every hour) | `grid_kwh + solar_used_kwh + battery_discharge_kwh = demand_kwh + battery_charge_kwh` |
| End-of-day neutrality | `battery_energy_after_kwh[23] = initial_energy_kwh` — battery can shift energy across hours but can't be a free one-time source |

Numeric tolerance for judge comparisons: **±0.01 kWh / ±0.01 BDT**.

---

## 6. Guardrails checklist (before trusting LLM output)

- [ ] `directive_type` ∈ the 6 allowed values
- [ ] `note_index` maps to a real note, each note appears exactly once
- [ ] `hours` unique, ascending, within 0–23
- [ ] `solar_reduction.factor` ∈ [0, 1]
- [ ] reserve/grid-cap values finite, non-negative, within physical limits
- [ ] `applies`/`structured_adjustment` semantics match the `no_op` vs. real-directive rule
- [ ] no invented demand/tariff/battery parameter changes
- [ ] final schedule replayed to confirm every extracted directive was actually followed

---

## 7. Deployment & submission requirements

| Item | Requirement |
|---|---|
| API service | One deployed HTTP service exposing both endpoints (not separate deployments) |
| Reachability | No login/VPN/dashboard/private network for the judge; reachable throughout the whole judging window, including repeated LLM-backed calls |
| Repository | Create a **new** GitHub repo *after* the question is revealed; keep **private during the event**, make **public after the submission deadline** |
| README.md | Self-contained: setup, env var names, model/provider or local model used, LLM role, guardrails, optimizer/solver used, exact run command, `/health` + sample `/optimize-energy` curl, dependencies, known limitations. **No secret values.** |
| Docker fallback image | Pullable (Docker Hub/GHCR/equivalent), exact tag/digest, documented run command, exposes documented port, binds `0.0.0.0`, **no baked-in secrets** |
| 3-minute video | Max 3 min, explains problem understanding, architecture (LLM → guardrails → optimizer), key implementation choices, how to run/test. Production polish not required. |
| Secrets | Never commit API keys/tokens/.env/passwords; never leak them in logs, prompts, or responses |
| Data | Only synthetic challenge data — no live campus/utility/billing/personal data |
| External model dependency | If using a hosted LLM API, your team owns keys/quota/rate-limits/availability — judges won't fix a broken dependency; a local/backup model is allowed |
| AI coding assistants | Permitted for scaffolding, but **core architecture/logic must be the team's own work**; credit external tools/deps in README |

---

## 8. Scoring — 100 points, fully automated

| # | Category | Points | What it checks |
|---|---|---|---|
| 1 | LLM Directive Interpretation | 25 | 5 relevance/no_op + 5 directive_type + 5 hours + 5 numeric values/shape + 5 paraphrase robustness |
| 2 | Directive Application & Constraint Correctness | 25 | 10 ground-truth directive applied correctly + 5 energy balance/effective-solar + 5 battery transitions/bounds/rates + 5 action consistency/neutrality/non-negativity |
| 3 | Optimization Quality | 10 | `min(1, organizer_optimal_cost / recalculated_team_cost)` averaged across valid hidden cases, ×10. Invalid cases score 0 here. |
| 4 | API Contract & Schema | 10 | 2 endpoint/status behavior + 2 request validation + 3 hourly_plan/top-level schema + 3 directive_interpretation schema/order/types |
| 5 | Performance & Reliability | 10 | 2 health readiness + 3 p95 latency + 3 valid-request stability/failure rate + 2 controlled malformed-input/model-failure handling & secret safety |
| 6 | Deployment & Docker Fallback | 10 | 3 live endpoint reachability + 4 working pullable Docker image + 2 clean startup from submitted instructions + 1 no manual debugging needed |
| 7 | Documentation & Local Reproducibility | 10 | 3 clean-environment quickstart + 2 env/model-provider docs + 2 public-sample test procedure + 1 architecture explanation + 1 Docker/fallback instructions + 1 secret-handling guidance |

**Video = 0 base points.** It's reviewed **only** as a tie-breaker when two+ teams have the same total score.

### Grading philosophy
> "The system is judged as a pipeline: understand the note, validate the structured directive, apply it to the optimization, return a valid schedule, and then optimize cost. A cheap schedule built on a wrong or ignored directive does not score as a correct solution."

Ground truth is always checked **before** cost — an invalid hidden case scores 0 on Optimization Quality regardless of how cheap it looks.

---

## 9. Critical violations & penalties

| Violation | Penalty |
|---|---|
| No real LLM in the interpretation path (or LLM only used for cosmetic text) | **Disqualifying** — ineligible for the final preliminary shortlist |
| Relevant note interpreted incorrectly / wrongly marked `no_op` | Interpretation credit lost for that note/case |
| Applicable ground-truth directive not reflected in `hourly_plan` | Case invalid for directive-application scoring; no optimization credit |
| Energy-balance failure / unmet hourly demand | Case invalid; no optimization credit |
| Battery bound/transition/rate violation | Case invalid; no optimization credit |
| Effective-solar overuse / impossible or negative values | Case invalid; no optimization credit |
| `no_charge_window` / `no_discharge_window` / reserve / `max_grid_window` violated | Case invalid; no optimization credit |
| End-of-day battery ≠ initial energy | Case invalid; no optimization credit |
| Reported totals disagree with recalculated `hourly_plan` | Scoring deduction; repeated failures can block qualification |

---

## 10. Tie-break order (only if total scores are equal)

1. 3-minute video review
2. Directive Application & Constraint Correctness
3. LLM Directive Interpretation
4. Optimization Quality
5. API/schema validity
6. Reliability & deployment stability
7. Documentation & local reproducibility
8. Exceptional engineering/verification (judge discretion)

---

## 11. Public sample cases (`BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`)

- **10 fully worked cases** (`SAMPLE-01` … `SAMPLE-10`), each with input, expected interpretation semantics, and one valid optimal reference schedule.
- These are for **local validation only** — not the hidden judge set. Don't hard-code case IDs, note wording, or numeric values from them.
- Equivalent optimal schedules (different but equally valid/cheap) are accepted — no byte-for-byte matching against the reference plan.
- Use them to sanity-check: POST each `case.input` to your `/optimize-energy`, diff your `directive_interpretation` against the expected semantics, and replay your `hourly_plan` against the constraint reminders baked into the file's `_meta` block.

---

## 12. Suggested build priority (per the official guide)

1. Exact API & JSON contract (get `/health` and the request/response schema byte-perfect first)
2. LLM operator-note interpretation
3. Deterministic guardrails
4. Directive application & energy correctness
5. Optimization quality (cost minimization)
6. Reliability, deployment, Docker fallback
7. Documentation & local reproducibility
8. 3-minute video (tie-break only — do this last)

---

## 13. Pre-submit checklist

- [ ] `GET /health` reachable, returns `{"status":"ok"}` within 60s of start
- [ ] `POST /optimize-energy` reachable externally, accepts exact request schema
- [ ] Every note → exactly one `directive_interpretation` entry, correct order/shape/`applies` semantics
- [ ] LLM output guardrailed before optimization; hours ascending 0–23, no invented constraints
- [ ] `hourly_plan` satisfies all ground-truth directives + energy/battery/rate/neutrality rules
- [ ] `total_grid_kwh`/`total_cost_bdt`/`peak_grid_kwh` match values recomputed from `hourly_plan`
- [ ] README is a clean, secret-free, copy-paste quickstart
- [ ] Repo created after question reveal, private during event, public after deadline
- [ ] Docker fallback image pullable, exact tag/digest, no baked-in secrets, reaches `/health`
- [ ] 3-minute video uploaded/linked and accessible
