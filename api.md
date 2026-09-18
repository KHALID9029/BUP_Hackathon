# GridWise LLM — API Reference

For client applications integrating with the GridWise energy scheduling service. This document
describes the two HTTP endpoints, their exact request/response shapes, error behavior, and
practical integration notes (timeouts, retries, latency).

Base URL: wherever the service is deployed (e.g. `http://localhost:8000` locally, or the deployed
public URL). All endpoints are relative to this base. No authentication is required to call this
API — the service holds its own upstream LLM credentials server-side.

Content type: `application/json` for both the request body and every response body.

---

## `GET /health`

Liveness check. Never touches the LLM or requires any upstream credentials — safe to poll.

**Request:** no body, no parameters.

**Response — `200 OK`**

```json
{ "status": "ok" }
```

There is no other status code for this endpoint.

---

## `POST /optimize-energy`

Takes one 24-hour campus energy scenario plus 1–3 free-text operator notes, and returns a
structured interpretation of each note alongside a cost-minimizing 24-hour schedule.

### Request body

```jsonc
{
  "scenario_id": "GRID-101",
  "operator_notes": [
    "Solar output will drop to about 20% from 1 PM to 3 PM.",
    "Do not charge the battery between 2 PM and 4 PM."
  ],
  "hours": [
    { "hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7 }
    // ... exactly 24 entries total, one per hour 0-23, any order ...
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

#### Field reference

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `scenario_id` | string | non-empty | Echoed back verbatim in the response. Free-form identifier — use it to correlate requests/responses client-side. |
| `operator_notes` | string[] | 1–3 items, each non-empty (non-whitespace) | Free-text natural-language notes. Any phrasing works — the LLM interprets them; no fixed vocabulary is required. |
| `hours` | object[] | exactly 24 items, hours 0–23 each appearing exactly once | Order in the array does not matter — the server sorts by `hour` before processing. |
| `hours[].hour` | int | 0–23 | |
| `hours[].demand_kwh` | float | ≥ 0 | Forecast demand for that hour. |
| `hours[].solar_kwh` | float | ≥ 0 | Forecast solar generation for that hour, before any `solar_reduction` directive is applied. |
| `hours[].tariff_bdt_per_kwh` | float | ≥ 0 | Grid import price for that hour, in BDT/kWh. |
| `battery` | object | — | |
| `battery.capacity_kwh` | float | > 0 | |
| `battery.initial_energy_kwh` | float | ≥ 0, and `minimum_energy_kwh ≤ initial_energy_kwh ≤ capacity_kwh` | Energy in the battery at the start of hour 0. |
| `battery.minimum_energy_kwh` | float | ≥ 0 | Base floor; a `minimum_battery_reserve` directive can raise this for specific hours, never lower it. |
| `battery.max_charge_kwh_per_hour` | float | ≥ 0 | Rate limit, charging. |
| `battery.max_discharge_kwh_per_hour` | float | ≥ 0 | Rate limit, discharging. |

Unknown/extra top-level or nested fields are silently ignored, not rejected.

### Response body — `200 OK`

```jsonc
{
  "scenario_id": "GRID-101",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": { "hours": [13, 14], "factor": 0.2 },
      "explanation": "Solar availability is reduced during panel cleaning."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "This note does not affect today's energy schedule."
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 150.0,
      "solar_used_kwh": 0.0,
      "battery_action": "charge",
      "battery_kwh": 30.0,
      "battery_energy_after_kwh": 230.0
    }
    // ... 24 entries total, hours 0-23 in order ...
  ],
  "total_grid_kwh": 3120.5,
  "total_cost_bdt": 21843.75,
  "peak_grid_kwh": 210.0,
  "plan_summary": "Applied 1 directive(s) (solar_reduction); ignored 1 unrelated note(s); ..."
}
```

#### Field reference

| Field | Type | Notes |
|---|---|---|
| `scenario_id` | string | Echoes the request. |
| `directive_interpretation` | object[] | Exactly one entry per input note, **in the same order as `operator_notes`** (`note_index` 0-based, matching the note's position in the request array). |
| `directive_interpretation[].note_index` | int | Index into the request's `operator_notes`. |
| `directive_interpretation[].applies` | bool | `false` only when `directive_type` is `"no_op"`; `true` for every real directive. |
| `directive_interpretation[].directive_type` | string enum | One of the 6 values below. |
| `directive_interpretation[].structured_adjustment` | object \| `null` | `null` iff `directive_type` is `"no_op"`. Shape depends on `directive_type` — see table below. |
| `directive_interpretation[].explanation` | string | One human-readable sentence. Not intended for programmatic parsing. |
| `hourly_plan` | object[] | Always exactly 24 entries, hour 0 through 23, in order. |
| `hourly_plan[].hour` | int | 0–23. |
| `hourly_plan[].grid_kwh` | float | Grid import for that hour. |
| `hourly_plan[].solar_used_kwh` | float | Solar actually consumed that hour (≤ effective forecast solar; excess is curtailed, never exported). |
| `hourly_plan[].battery_action` | string enum | `"charge"`, `"discharge"`, or `"idle"`. |
| `hourly_plan[].battery_kwh` | float | Magnitude of charge/discharge that hour; `0` when idle. |
| `hourly_plan[].battery_energy_after_kwh` | float | Battery state of charge at the end of that hour. |
| `total_grid_kwh` | float | Sum of `hourly_plan[].grid_kwh`, recomputed from the plan (not an internal solver value). |
| `total_cost_bdt` | float | `Σ grid_kwh[h] * tariff_bdt_per_kwh[h]`, recomputed from the plan. |
| `peak_grid_kwh` | float | `max(hourly_plan[].grid_kwh)`, recomputed from the plan. |
| `plan_summary` | string | Deterministic, human-readable one-paragraph summary. Not intended for programmatic parsing — display as-is if useful, don't pattern-match it. |

#### `directive_type` values and `structured_adjustment` shapes

| `directive_type` | `structured_adjustment` shape | Meaning |
|---|---|---|
| `"solar_reduction"` | `{ "hours": int[], "factor": float }` | Usable solar in `hours` is multiplied by `factor` (0–1; the *remaining* fraction, not the reduction — e.g. "80% reduction" → `factor: 0.2`). |
| `"minimum_battery_reserve"` | `{ "hours": int[], "minimum_energy_kwh": float }` | Battery energy must stay ≥ `minimum_energy_kwh` at the end of each hour in `hours`. |
| `"no_charge_window"` | `{ "hours": int[] }` | Battery cannot charge during `hours`. |
| `"no_discharge_window"` | `{ "hours": int[] }` | Battery cannot discharge during `hours`. |
| `"max_grid_window"` | `{ "hours": int[], "max_grid_kwh": float }` | Grid import during `hours` cannot exceed `max_grid_kwh` per hour. |
| `"no_op"` | `null` | Note doesn't affect today's schedule (irrelevant/administrative/distractor). `applies` is always `false` here. |

`hours` arrays are always unique integers 0–23 in ascending order. An overnight note (e.g. "11 PM
until 4 AM") is already expanded into the correct wrapped set (`[0, 1, 2, 3, 23]`) — clients never
need to interpret time windows themselves.

### Error responses

| Status | Body shape | When |
|---|---|---|
| `400` | `{ "error": "invalid_request", "detail": [...] }` | Malformed JSON, wrong JSON shape (e.g. array instead of object), or any schema/constraint violation on the request body — see table below. |
| `422` | `{ "error": "infeasible_scenario", "detail": "<message>" }` | The request is well-formed, but no valid 24-hour schedule exists given the directives and battery limits (should not occur for organizer-valid scenarios). |
| `500` | `{ "error": "llm_not_configured" }` | Server has no upstream LLM key configured. Only possible on this endpoint — `/health` is unaffected. |
| `500` | `{ "error": "internal_error" }` | Any other unexpected server-side failure. The body is always this fixed string — never a stack trace or internal detail. |

`detail` in a `400` response is a list of `{ "loc": [...], "msg": "..." }` objects, one per
violation, pointing at the offending field path — useful for surfacing per-field form errors in a
client UI. Common triggers:

- `hours` not exactly 24 entries, or not covering 0–23 exactly once
- `operator_notes` empty, more than 3 entries, or containing a blank string
- Any numeric field violating its constraint (e.g. negative `demand_kwh`)
- `battery.initial_energy_kwh` outside `[minimum_energy_kwh, capacity_kwh]`
- Request body is not valid JSON, or is JSON but not an object

### Example

**Request**

```bash
curl -X POST https://<deployed-host>/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "SAMPLE-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
      "The sports office moved next month'\''s registration deadline."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 20, "tariff_bdt_per_kwh": 10},
      {"hour": 8, "demand_kwh": 150, "solar_kwh": 50, "tariff_bdt_per_kwh": 12},
      {"hour": 9, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 175, "solar_kwh": 130, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 180, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 185, "solar_kwh": 180, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 180, "solar_kwh": 170, "tariff_bdt_per_kwh": 14},
      {"hour": 14, "demand_kwh": 170, "solar_kwh": 140, "tariff_bdt_per_kwh": 13},
      {"hour": 15, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 45, "tariff_bdt_per_kwh": 18},
      {"hour": 17, "demand_kwh": 185, "solar_kwh": 10, "tariff_bdt_per_kwh": 22},
      {"hour": 18, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
      {"hour": 19, "demand_kwh": 215, "solar_kwh": 0, "tariff_bdt_per_kwh": 30},
      {"hour": 20, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
      {"hour": 21, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 135, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
      {"hour": 23, "demand_kwh": 105, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
    ],
    "battery": {
      "capacity_kwh": 220,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

**Response — `200 OK`** (captured from a live run)

```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": { "hours": [12, 13], "factor": 0.25 },
      "explanation": "Rooftop solar panel washing reduces usable solar to 25% from noon to 2 PM."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "Administrative note about next month's registration deadline does not affect today's operations."
    }
  ],
  "hourly_plan": [
    { "hour": 0, "grid_kwh": 90.0, "solar_used_kwh": 0.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 110.0 },
    { "hour": 1, "grid_kwh": 45.0, "solar_used_kwh": 0.0, "battery_action": "discharge", "battery_kwh": 40.0, "battery_energy_after_kwh": 70.0 },
    { "hour": 2, "grid_kwh": 130.0, "solar_used_kwh": 0.0, "battery_action": "charge", "battery_kwh": 50.0, "battery_energy_after_kwh": 120.0 },
    { "hour": 3, "grid_kwh": 130.0, "solar_used_kwh": 0.0, "battery_action": "charge", "battery_kwh": 50.0, "battery_energy_after_kwh": 170.0 },
    { "hour": 4, "grid_kwh": 135.0, "solar_used_kwh": 0.0, "battery_action": "charge", "battery_kwh": 50.0, "battery_energy_after_kwh": 220.0 },
    { "hour": 5, "grid_kwh": 95.0, "solar_used_kwh": 0.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 220.0 },
    { "hour": 6, "grid_kwh": 105.0, "solar_used_kwh": 5.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 220.0 },
    { "hour": 7, "grid_kwh": 110.0, "solar_used_kwh": 20.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 220.0 },
    { "hour": 8, "grid_kwh": 100.0, "solar_used_kwh": 50.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 220.0 },
    { "hour": 9, "grid_kwh": 75.0, "solar_used_kwh": 90.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 220.0 },
    { "hour": 10, "grid_kwh": 0.0, "solar_used_kwh": 130.0, "battery_action": "discharge", "battery_kwh": 45.0, "battery_energy_after_kwh": 175.0 },
    { "hour": 11, "grid_kwh": 0.0, "solar_used_kwh": 160.0, "battery_action": "discharge", "battery_kwh": 20.0, "battery_energy_after_kwh": 155.0 },
    { "hour": 12, "grid_kwh": 90.0, "solar_used_kwh": 45.0, "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 105.0 },
    { "hour": 13, "grid_kwh": 152.5, "solar_used_kwh": 42.5, "battery_action": "charge", "battery_kwh": 15.0, "battery_energy_after_kwh": 120.0 },
    { "hour": 14, "grid_kwh": 80.0, "solar_used_kwh": 140.0, "battery_action": "charge", "battery_kwh": 50.0, "battery_energy_after_kwh": 170.0 },
    { "hour": 15, "grid_kwh": 125.0, "solar_used_kwh": 90.0, "battery_action": "charge", "battery_kwh": 50.0, "battery_energy_after_kwh": 220.0 },
    { "hour": 16, "grid_kwh": 125.0, "solar_used_kwh": 45.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 220.0 },
    { "hour": 17, "grid_kwh": 145.0, "solar_used_kwh": 10.0, "battery_action": "discharge", "battery_kwh": 30.0, "battery_energy_after_kwh": 190.0 },
    { "hour": 18, "grid_kwh": 155.0, "solar_used_kwh": 0.0, "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 140.0 },
    { "hour": 19, "grid_kwh": 165.0, "solar_used_kwh": 0.0, "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 90.0 },
    { "hour": 20, "grid_kwh": 155.0, "solar_used_kwh": 0.0, "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 40.0 },
    { "hour": 21, "grid_kwh": 175.0, "solar_used_kwh": 0.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 40.0 },
    { "hour": 22, "grid_kwh": 155.0, "solar_used_kwh": 0.0, "battery_action": "charge", "battery_kwh": 20.0, "battery_energy_after_kwh": 60.0 },
    { "hour": 23, "grid_kwh": 155.0, "solar_used_kwh": 0.0, "battery_action": "charge", "battery_kwh": 50.0, "battery_energy_after_kwh": 110.0 }
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Applied 1 directive(s) (solar_reduction); ignored 1 unrelated note(s); shifted 335.0 kWh through the battery from cheaper to costlier hours; restored the battery to 110 kWh at end of day; total grid cost 38365.00 BDT."
}
```

**Example `400` response** (`hours` missing entries)

```json
{
  "error": "invalid_request",
  "detail": [
    { "loc": ["body", "hours"], "msg": "List should have at least 24 items after validation, not 0" }
  ]
}
```

---

## Integration notes for client apps

- **No auth header needed.** The service manages its own upstream LLM credentials; clients call it directly.
- **Set a client-side timeout of at least 30s** per request — this matches the service's own hard per-request budget. Typical latency for `/optimize-energy` is 3–6s (single request), and can rise to ~9–10s p95 under heavy concurrent load, since each call involves a live LLM round-trip.
- **`/optimize-energy` never times out silently** — every response is either a complete `200` schedule or one of the error shapes above; there is no partial/streaming response.
- **Poll `/health`** for readiness/liveness checks — it responds immediately and never depends on the LLM, so it's safe to call frequently (e.g. before enabling a "submit" button, or for a status indicator).
- **`directive_interpretation` order is stable** and always matches `operator_notes` order — safe to zip the two arrays client-side by index for a "note → what we did with it" UI.
- **Numeric tolerance:** treat `total_grid_kwh`, `total_cost_bdt`, and `peak_grid_kwh` as already reconciled with `hourly_plan` — no need to recompute them client-side, though they're safe to recompute for a sanity-check display since the formulas are given above.
- **Don't parse `explanation` or `plan_summary` programmatically** — they're free-text, meant for display only, and their wording is not guaranteed stable across requests or LLM model changes.
- **A `no_op` note is a valid, expected outcome**, not an error — distractor/irrelevant notes are common by design; render `applies: false` entries distinctly (e.g. "ignored") rather than treating them as failures.
