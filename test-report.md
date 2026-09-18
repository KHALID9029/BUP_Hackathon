# GridWise LLM — Test Report

Date: 2026-09-18
Model under test: `google/gemini-3.8-flash` (primary, via OpenRouter), fallbacks `openai/gpt-4.1-mini` → `deepseek/deepseek-v4.1-flash`
Scope: full pipeline — LLM interpreter → guardrails → LP optimizer → final replay validator → API — exercised offline and live, including inside the built Docker image.

## Summary

All test categories passed. The one notable risk is **p95 latency under concurrency**, which exceeded the value documented in the README and is close to the rubric's "full points" threshold. Everything else — interpretation accuracy (including paraphrases), guardrails, optimizer correctness, API contract, Docker fallback behavior, and secret handling — held up under every case tested.

| Category | Result |
|---|---|
| Offline unit tests (`pytest`) | 36/36 passed |
| Public sample cases, live HTTP | 10/10 passed, cost ratio 1.0000 on every case |
| Paraphrase robustness bench | 10/10 passed |
| API contract edge cases | All correct (400/422/500 as specified) |
| Docker build & fallback health check | Passed |
| Docker with real key, full samples | 10/10 passed |
| Secret hygiene | No leaks in any response body or log line |
| Concurrency (20 parallel real LLM calls) | 0 failures, 0 leaks, **p95 latency high (see Findings)** |

---

## 1. Offline test suite

```
uv run pytest -v
```

Result: **36/36 passed**, 0 warnings of consequence.

Coverage confirmed present and green:
- `tests/test_api.py` (10 tests): health never touches the LLM, malformed JSON → 400, 23 hours → 400, duplicate hour → 400, 4 notes → 400, empty note → 400, missing key → controlled 500 on POST only, LLM exception degrades to 200 with `no_op`, extra fields ignored + `scenario_id` echoed, response never contains the API key.
- `tests/test_guardrails.py` (10 tests): overnight window expansion, zero-length window handling, missing/duplicate/out-of-range note index, percent-based solar factor, factor clamped above 1, percent-of-capacity reserve conversion, reserve clamped to capacity, zero-length window rejected for non-`no_op`, negative grid cap rejected, `None` LLM output degrades all notes to `no_op`.
- `tests/test_optimizer.py` (14 tests): all 10 public samples reproduce the reference `total_cost_bdt` exactly and pass `replay`, end-of-day neutrality, no simultaneous charge/discharge, infeasible reserve correctly raises, solar curtailment when solar exceeds demand.

## 2. Live public sample cases (real Gemini 3.8 Flash calls)

```
uv run python scripts/run_samples.py http://localhost:8000
```

Result: **10/10 passed**, every case's `total_cost_bdt` matched the organizer reference exactly (ratio `1.0000`), and every returned plan passed `validator.replay` with 0 errors. Wall time ≈ 36s for 10 sequential requests (~3.6s/request average), consistent with the plan's target.

## 3. Paraphrase robustness (`bench/paraphrases.json`)

Ran all 10 hand-written paraphrase cases against the live server (ad hoc script, same matching logic as `run_samples.py`, since it wasn't previously wired to a runner):

Result: **10/10 passed**, covering:
- 24-hour clock phrasing (`01:00`–`04:00`) for `max_grid_window`
- "at or below" + "noon" phrasing
- Overnight wrap (`11 PM` → `4 AM`) for `no_discharge_window`
- "midnight" as a window start for `no_charge_window`
- "one-fifth" + "noon" for `solar_reduction`
- "80% reduction" (equivalent to `factor=0.2`) for `solar_reduction`
- Percent-of-capacity reserve ("half of the battery's capacity") + "midnight" as a window end
- Distractor notes (mentions battery/solar but states no constraint) mixed with a real directive, 2-note requests
- 3-note request mixing `no_charge_window`, absolute reserve, and a `no_op` distractor

All directive types, hours, and numeric values matched expected output exactly; all plans passed replay.

## 4. API contract edge cases (live HTTP)

| Case | Expected | Observed |
|---|---|---|
| Malformed JSON body | 400 | 400 `invalid_request` |
| Empty body | 400 | 400 `invalid_request` |
| JSON array instead of object | 400 | 400 `invalid_request` |
| Wrong `Content-Type` (`text/plain`) with valid JSON | 400 | 400 `invalid_request` |
| 23 hours (one missing) | 400 | 400 `invalid_request` |
| Duplicate hour value | 400 | 400 `invalid_request` |
| 4 operator notes | 400 | 400 `invalid_request` |
| Blank/whitespace-only note | 400 | 400 `invalid_request` |
| Negative `demand_kwh` | 400 | 400 `invalid_request` |
| `initial_energy_kwh` outside `[minimum_energy_kwh, capacity_kwh]` | 400 | 400 `invalid_request` |
| Extra unknown top-level field | 200, field dropped | 200, field absent from response |
| `scenario_id` echo | Echoed verbatim | Echoed verbatim |
| Infeasible scenario (reserve directive unreachable given rate limits), driven end-to-end through a real LLM-interpreted note | 422 | 422 `infeasible_scenario`, message contains only the HiGHS status string — no internals leaked |

## 5. Docker

```
docker build -t gridwise-test:local .
```
Built successfully.

**Fallback / health-readiness check** (container run **without** `OPENROUTER_API_KEY`):
- `GET /health` → 200 `{"status":"ok"}`
- `POST /optimize-energy` → 500 `{"error":"llm_not_configured"}` (controlled, no stack trace)

**Full pipeline inside the container** (run **with** the real key and `LLM_MODEL=google/gemini-3.8-flash`):
- `GET /health` → 200
- All 10 public samples via `scripts/run_samples.py` → **10/10 passed**, same exact cost matches as the non-Docker run.

Checked container logs for key leakage: **0 occurrences** of the API key value across all requests, including the no-key 500 path and the full-pipeline run.

## 6. Concurrency / reliability

20 concurrent real requests (`asyncio.gather`, real Gemini 3.8 Flash calls, `LLM_CONCURRENCY=20` from `.env`):

```
N=20  wall_clock=9.55s  p50=4.85s  p95=9.40s  max=9.53s  failures=0  key_leaks=0
```

Single-request baseline (3 sequential calls, no concurrency): **2.7s – 5.8s**, already brushing the 5s threshold on some calls.

No failures, no retries observed in server logs (every request settled in a single OpenRouter call), no secret leakage.

---

## Findings

### 1. p95 latency under load exceeds the documented figure (latency risk, not a defect)

- **Observed:** p95 = 9.4s at 20-way concurrency; single-request latency alone ranged 2.7–5.8s.
- **README claims:** "observed ≈5.6s in local testing" under a similar burst.
- **Impact:** Per the rubric (Section 5, Performance & Reliability), p95 in the 5–15s band scores 2/3 instead of full points on the latency sub-score. This is gateway/model-side — Gemini 3.8 Flash's reasoning floor (`effort: low`, the lowest it accepts) plus OpenRouter-side queuing under one API key — not an application bug; the code already has the correct structural mitigations (semaphore, hard per-attempt timeout, fallback chain).
- **Not a functional failure:** 0 failures, 0 timeouts, 0 leaked secrets even at this latency.
- **Suggested follow-up (decision, not applied):** re-run `scripts/bench_models.py`'s full protocol close to the judging window to re-check whether `openai/gpt-4.1-mini` (non-reasoning, documented as consistently low-latency) should be promoted to primary if the p95 gap doesn't close, or lower `LLM_CONCURRENCY` to trade queuing latency for fewer parallel gateway calls.

No other issues found. Interpretation accuracy (including paraphrased/ambiguous notes and distractors), guardrail normalization, optimizer/LP correctness, final-replay validation, API contract behavior, Docker fallback health-check, and secret hygiene all passed every case tried, both locally and inside the built Docker image.
