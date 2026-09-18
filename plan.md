# GridWise LLM — Implementation Plan (LangGraph + FastAPI)

Goal: one HTTP service (`GET /health`, `POST /optimize-energy`) that runs the mandated pipeline
**LLM interpreter → deterministic guardrails → LP optimizer → final replay validator → response**,
built as a LangGraph state machine. Everything below is written so the code can be typed in
directly during the 4-hour window. Section 12 is the time budget; Section 13 is the test phase.

Sources used while planning (credit these in README, see Section 11):
- LangChain OpenRouter integration: https://docs.langchain.com/oss/python/integrations/chat/openrouter
- `ChatOpenRouter` reference: https://reference.langchain.com/python/langchain-openrouter/chat_models/ChatOpenRouter
- PyPI `langchain-openrouter` (0.2.x): https://pypi.org/project/langchain-openrouter/
- OpenRouter model pages: https://openrouter.ai/deepseek/deepseek-v4.1-flash , https://openrouter.ai/google/gemini-3.8-flash , https://openrouter.ai/openai
- OpenRouter reasoning control: https://openrouter.ai/docs/use-cases/reasoning-tokens
- Gemini 3.8 Flash model page (thinking levels): https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash

---

## 1. Decisions (locked)

| Concern | Decision | Why |
|---|---|---|
| Orchestration | LangGraph `StateGraph` with a Pydantic state; 5 nodes; one conditional edge (guardrail → retry LLM once) | Mandated pipeline is linear; the retry loop is the one thing a graph buys us |
| LLM access | `langchain-openrouter` → `ChatOpenRouter(...).with_structured_output(LLMInterpretation, method="json_schema")` | Library, not hand-rolled HTTP; schema-enforced output |
| LLM call shape | **One call per request** covering all 1–3 notes | Keeps p95 flat, < 5 s target |
| Model choice | **Bake-off of 3 candidates** on 3 providers: `deepseek/deepseek-v4.1-flash`, `google/gemini-3.8-flash`, `openai/gpt-4.1-mini`. Same graph, same prompt, same cases; winner = primary, other two = fallbacks in order (Section 6.2) | Team decision; provider diversity survives a single-vendor outage; the data picks the winner, not opinion |
| Reasoning/thinking | Off or lowest level per model (`reasoning={"effort": ...}`) | All three candidates expose reasoning; thinking tokens are the fastest way to blow the 5 s p95 |
| Time windows from LLM | LLM emits `start_hour`/`end_hour` (end-exclusive); **code** expands to the `hours` list | Removes the classic off-by-one; overnight wrap handled deterministically |
| Relative values | LLM may emit `minimum_energy_percent_of_capacity`; guardrail converts using the request's battery capacity | Sample-03 ("50% of capacity" → 100 kWh) |
| Optimizer | Linear program, `scipy.optimize.linprog(method="highs")`, 120 variables, 48 equality rows | Exact optimum in milliseconds; no MILP needed because charge/discharge are netted post-solve |
| Final validator | Pure-Python hour-by-hour replay of the plan against the applied directives; totals recomputed from the plan | Judge does the same; we fail closed before responding |
| `plan_summary` | Deterministic template string (no second LLM call) | Latency; summary is not scored |
| HTTP errors | 400 for malformed/structurally invalid JSON (override FastAPI's default 422), 422 for infeasible scenario, 500 = `{"error":"internal_error"}` only | Contract in the problem statement |
| LLM failure | primary model → fallback model (via `with_fallbacks`) → one retry with guardrail feedback → **degrade**: unresolved notes become `no_op` with an explanation; still return a valid 200 schedule | "Safe failure": never crash, never invent a directive |
| Startup | `/health` never depends on the LLM: the API key is optional in settings, the graph is built lazily on the first `POST`, and a missing key yields a controlled error there | Health readiness (2 pts) and the Docker fallback check (4 pts) must pass even with no key configured |
| Concurrency | `asyncio.Semaphore(LLM_CONCURRENCY)` around the LLM call | Judge fires parallel requests; one OpenRouter key rate-limits with 429s |

---

## 2. Repository layout

```
gridwise/
├── app/
│   ├── __init__.py
│   ├── config.py        # pydantic-settings: env var names only, no values
│   ├── schemas.py       # request / response / directive models + LLM output model
│   ├── state.py         # LangGraph GraphState (Pydantic)
│   ├── llm.py           # ChatOpenRouter factory + prompt builder
│   ├── guardrails.py    # deterministic normalisation / rejection
│   ├── optimizer.py     # constraints builder + LP solve + plan extraction
│   ├── validator.py     # replay + totals
│   ├── summary.py       # plan_summary template
│   ├── graph.py         # build_graph()
│   └── main.py          # FastAPI app, endpoints, exception handlers
├── scripts/
│   └── run_samples.py   # POST all public samples, diff interpretation, replay plan, compare cost
├── tests/               # written by the team in the test phase (Section 13)
├── Dockerfile
├── pyproject.toml
├── .env.example         # names only
└── README.md
```

Dependencies (`pyproject.toml`): `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`,
`langgraph`, `langchain-core`, `langchain-openrouter`, `scipy`, `numpy`, `httpx` (tests). Python 3.12.

---

## 3. `app/config.py`

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    OPENROUTER_API_KEY: str | None = None        # optional at startup so /health works without it; ChatOpenRouter reads it from env
    # Bake-off candidates (Section 6.2). The bench script writes the winner into .env as LLM_MODEL and the
    # other two, best first, into LLM_FALLBACK_MODELS. Defaults below are only a starting order.
    LLM_MODEL: str = "deepseek/deepseek-v4.1-flash"
    LLM_FALLBACK_MODELS: str = "google/gemini-3.8-flash,openai/gpt-4.1-mini"   # comma-separated, tried in order
    LLM_TIMEOUT_S: float = 10.0                  # per attempt; hard cap via asyncio.wait_for
    LLM_MAX_ATTEMPTS: int = 2                    # 1 normal + 1 retry with guardrail feedback
    LLM_MAX_TOKENS: int = 1000                   # 3 directives ≈ 350 tokens; headroom avoids truncation → retry → latency
    LLM_CONCURRENCY: int = 8                     # semaphore around the OpenRouter call
    PORT: int = 8000


settings = Settings()
```

`.env.example` lists the same names with empty values.

---

## 4. `app/schemas.py` — Pydantic models

### 4.1 Request (drives the 400 behaviour)

```python
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DirectiveType = Literal[
    "solar_reduction", "minimum_battery_reserve", "no_charge_window",
    "no_discharge_window", "max_grid_window", "no_op",
]
BatteryAction = Literal["charge", "discharge", "idle"]


class HourInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Field(ge=0, allow_inf_nan=False)
    solar_kwh: float = Field(ge=0, allow_inf_nan=False)
    tariff_bdt_per_kwh: float = Field(ge=0, allow_inf_nan=False)


class BatterySpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capacity_kwh: float = Field(gt=0, allow_inf_nan=False)
    initial_energy_kwh: float = Field(ge=0, allow_inf_nan=False)
    minimum_energy_kwh: float = Field(ge=0, allow_inf_nan=False)
    max_charge_kwh_per_hour: float = Field(ge=0, allow_inf_nan=False)
    max_discharge_kwh_per_hour: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _consistent(self) -> "BatterySpec":
        if not (self.minimum_energy_kwh <= self.initial_energy_kwh <= self.capacity_kwh):
            raise ValueError("initial_energy_kwh must lie within [minimum_energy_kwh, capacity_kwh]")
        return self


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scenario_id: str = Field(min_length=1)
    operator_notes: list[str] = Field(min_length=1, max_length=3)
    hours: list[HourInput] = Field(min_length=24, max_length=24)
    battery: BatterySpec

    @field_validator("operator_notes")
    @classmethod
    def _notes_non_empty(cls, v: list[str]) -> list[str]:
        if any(not n.strip() for n in v):
            raise ValueError("operator_notes must be non-empty strings")
        return v

    @field_validator("hours")
    @classmethod
    def _hours_complete(cls, v: list[HourInput]) -> list[HourInput]:
        if sorted(h.hour for h in v) != list(range(24)):
            raise ValueError("hours must contain each hour 0..23 exactly once")
        return sorted(v, key=lambda h: h.hour)
```

### 4.2 Directive interpretation (response side, strict shapes)

```python
class SolarReductionAdjustment(BaseModel):
    hours: list[int]
    factor: float

class ReserveAdjustment(BaseModel):
    hours: list[int]
    minimum_energy_kwh: float

class WindowAdjustment(BaseModel):
    hours: list[int]

class GridCapAdjustment(BaseModel):
    hours: list[int]
    max_grid_kwh: float

Adjustment = SolarReductionAdjustment | ReserveAdjustment | WindowAdjustment | GridCapAdjustment


class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[Adjustment]
    explanation: str

    @model_validator(mode="after")
    def _applies_semantics(self) -> "DirectiveInterpretation":
        if self.directive_type == "no_op":
            assert self.applies is False and self.structured_adjustment is None
        else:
            assert self.applies is True and self.structured_adjustment is not None
        return self
```

These are only ever constructed by the guardrail, so the validator is a self-check, not user-facing.

### 4.3 LLM output model (what `with_structured_output` enforces)

Deliberately *looser* than the response model: windows instead of hour lists, optional numeric
fields, an explicit percent-of-capacity slot. The guardrail turns this into 4.2.

```python
class TimeWindow(BaseModel):
    start_hour: int = Field(ge=0, le=23, description=(
        "First hour INCLUDED, 24-hour clock. midnight=0, noon=12, 1 PM=13, 6 PM=18."))
    end_hour: int = Field(ge=0, le=24, description=(
        "First hour EXCLUDED, 24-hour clock. '1 PM to 3 PM' -> start_hour=13, end_hour=15. "
        "'until midnight' -> 24. Overnight windows may have end_hour < start_hour."))


class LLMDirective(BaseModel):
    note_index: int = Field(description="Index of the operator note this entry interprets (0-based).")
    directive_type: DirectiveType
    windows: list[TimeWindow] = Field(default_factory=list, description="Empty for no_op.")
    solar_remaining_fraction: Optional[float] = Field(None, description=(
        "solar_reduction only. Fraction of forecast solar that REMAINS usable, 0..1. "
        "'drops to 20%' -> 0.2; '80% reduction' -> 0.2; 'half' -> 0.5; 'one-fifth' -> 0.2; 'no solar' -> 0."))
    minimum_energy_kwh: Optional[float] = Field(None, description=(
        "minimum_battery_reserve only, absolute kWh stated in the note. Leave null if the note is a percentage."))
    minimum_energy_percent_of_capacity: Optional[float] = Field(None, description=(
        "minimum_battery_reserve only, 0..100, when the note says a percentage/fraction of battery capacity."))
    max_grid_kwh: Optional[float] = Field(None, description="max_grid_window only, kWh per hour cap on grid import.")
    explanation: str = Field(description="One short sentence.")


class LLMInterpretation(BaseModel):
    directives: list[LLMDirective] = Field(description="Exactly one entry per operator note, in note_index order.")
```

### 4.4 Response

```python
class HourPlan(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: BatteryAction
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourPlan]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
```

---

## 5. `app/state.py` — LangGraph state

```python
from pydantic import BaseModel, Field
from .schemas import OptimizeRequest, LLMInterpretation, DirectiveInterpretation, HourPlan


class GraphState(BaseModel):
    request: OptimizeRequest
    attempts: int = 0
    raw: LLMInterpretation | None = None          # last LLM output (None on failure)
    llm_error: str | None = None                  # exception class name only, never the message (may contain key info)
    problems: list[str] = Field(default_factory=list)   # guardrail findings fed back on retry
    directives: list[DirectiveInterpretation] = Field(default_factory=list)
    plan: list[HourPlan] = Field(default_factory=list)
    total_grid_kwh: float = 0.0
    total_cost_bdt: float = 0.0
    peak_grid_kwh: float = 0.0
    plan_summary: str = ""
    warnings: list[str] = Field(default_factory=list)   # logged, never returned
```

Nodes receive a `GraphState` and return a partial `dict`; LangGraph merges it.

---

## 6. `app/llm.py` — OpenRouter via LangChain

```python
from langchain_openrouter import ChatOpenRouter
from langchain_core.messages import SystemMessage, HumanMessage
from .config import settings
from .schemas import LLMInterpretation, OptimizeRequest
import json


class LLMNotConfigured(RuntimeError):
    pass


# Lowest reasoning setting each candidate accepts (OpenRouter `reasoning.effort`).
# Gemini 3.8 Flash documents low/medium/high only ("minimal" errors) -> "low".
# DeepSeek V4.1 Flash and GPT-4.1-mini: try "none" first; if the API rejects it, the bench script will show
# a 4xx and you switch that entry to "low"/"minimal".
REASONING = {
    "deepseek/deepseek-v4.1-flash": {"effort": "none"},
    "google/gemini-3.8-flash": {"effort": "low"},
    "openai/gpt-4.1-mini": None,                     # non-reasoning model; send nothing
}


def make_chat(model: str):
    """One structured-output chat model for a given OpenRouter id. Used by build_llm() and by the bench script."""
    kwargs = dict(
        model=model,
        api_key=settings.OPENROUTER_API_KEY,
        temperature=0,
        max_tokens=settings.LLM_MAX_TOKENS,
        max_retries=1,
        # NOTE: reference docs say ChatOpenRouter.timeout is in milliseconds. We do not rely on it;
        # the graph node wraps the call in asyncio.wait_for(settings.LLM_TIMEOUT_S).
    )
    if REASONING.get(model):
        kwargs["reasoning"] = REASONING[model]       # ChatOpenRouter exposes `reasoning`; if the installed version
                                                     # does not, pass model_kwargs={"reasoning": {...}} instead
    chat = ChatOpenRouter(**kwargs)
    # If a model rejects strict json_schema, switch that model to method="function_calling".
    return chat.with_structured_output(LLMInterpretation, method="json_schema")


def build_llm():
    if not settings.OPENROUTER_API_KEY:
        raise LLMNotConfigured("OPENROUTER_API_KEY is not set")
    fallbacks = [m.strip() for m in settings.LLM_FALLBACK_MODELS.split(",") if m.strip()]
    primary = make_chat(settings.LLM_MODEL)
    return primary.with_fallbacks([make_chat(m) for m in fallbacks]) if fallbacks else primary


SYSTEM_PROMPT = """You convert campus energy operator notes into structured directives for a 24-hour scheduler.

Directive types (choose exactly one per note):
- solar_reduction: usable solar is reduced in some hours. Give solar_remaining_fraction (fraction that REMAINS).
- minimum_battery_reserve: battery energy must stay at or above a level in some hours. Give minimum_energy_kwh,
  or minimum_energy_percent_of_capacity if the note is relative to capacity.
- no_charge_window: battery may not charge in some hours (charger isolated/unavailable/disabled/maintenance).
- no_discharge_window: battery may not discharge in some hours (protection/relay testing, "do not discharge").
- max_grid_window: grid import per hour may not exceed a value in some hours (feeder/transformer/substation limit).
- no_op: the note does not change today's demand, solar, tariff, battery or grid limits
  (announcements, bookings, deadlines, things happening next week/month, general chatter).

Rules:
- Return exactly one entry per note, note_index 0..N-1, in order. Never skip or duplicate a note.
- Time windows: start_hour is included, end_hour is excluded. "1 PM to 3 PM" -> 13 to 15. "noon until 2 PM" -> 12 to 14.
  "from 6 PM until 9 PM" -> 18 to 21. "2 AM until 5 AM" -> 2 to 5. Use 24-hour clock.
- Percentages: "drops to 20%" and "80% reduction" both mean solar_remaining_fraction = 0.2. "about half" = 0.5.
- Do not invent numbers that are not in the note. Do not change demand, tariff or battery parameters.
- Only the six directive types above exist. If unsure whether a note is operational, prefer no_op.
"""


def build_messages(req: OptimizeRequest, feedback: list[str]) -> list:
    payload = {
        "battery": req.battery.model_dump(),
        "notes": [{"note_index": i, "text": n} for i, n in enumerate(req.operator_notes)],
    }
    human = "Interpret these notes.\n" + json.dumps(payload, indent=1)
    if feedback:
        human += ("\n\nYour previous answer had these problems; fix them and answer again:\n- "
                  + "\n- ".join(feedback))
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)]
```

Battery parameters are in the prompt only so the model can resolve "50% of capacity"; the guardrail
still recomputes it from the request, never from the model's arithmetic.

### 6.2 Model bake-off — pick the primary with data

Three candidates, three providers (ids verified on OpenRouter while planning; re-verify on event day):

| Candidate | Provider | Why it is in the race | Watch-outs |
|---|---|---|---|
| `deepseek/deepseek-v4.1-flash` | DeepSeek | Very cheap, huge throughput, tool/JSON support | Has a reasoning mode: force `effort: none` (or the lowest accepted); check p95 |
| `google/gemini-3.8-flash` | Google | Strongest Flash-class instruction following; structured output supported | Thinking cannot be fully disabled (`low` is the floor) and it uses more tokens than 3.7; check p95 hardest here |
| `openai/gpt-4.1-mini` | OpenAI | Non-reasoning, native strict `json_schema`, consistently low gateway latency | Slightly pricier per token; irrelevant at this volume |

Alternative for the OpenAI slot if 4.1-mini underperforms: `openai/gpt-5-nano` (cheaper, but reasoning-capable, so set `effort: none`).

**Protocol** (`scripts/bench_models.py`, run in the 1:30–1:55 slot, and again on the deployed host if time allows):

1. Case set = the 10 public samples **plus** `bench/paraphrases.json`: 10 hand-written notes reusing sample
   energy data with the interpretation filled in by the team (24h clock, "noon"/"midnight", "one-fifth",
   "80% reduction", "at or below", percent-of-capacity reserve, overnight wrap, two distractors that
   mention the battery/solar but state no constraint, three notes in one request).
2. For each model: `graph = build_graph(llm=make_chat(model))`, run every case **3 times** (temperature 0
   is not deterministic through a gateway), sequentially, then 10 requests concurrently once.
3. Score per model:
   - `accuracy`: fraction of notes with exact `directive_type` + `hours` + numeric value (±0.01) match, over all runs
   - `p50`, `p95` end-to-end latency, `failures` (exceptions, timeouts, schema parse errors, guardrail problems)
4. **Selection rule**: highest `accuracy`; tie → lowest `p95`; disqualify any model with `p95 > 5 s` or
   `failures > 0` on the sequential runs. Winner → `LLM_MODEL`; the other two, ranked by the same rule →
   `LLM_FALLBACK_MODELS`. Write the table into README (Section 11) as the justification.

```python
# scripts/bench_models.py
import asyncio, json, statistics, sys, time
from app.graph import build_graph
from app.llm import make_chat
from app.schemas import OptimizeRequest
from app.state import GraphState

MODELS = ["deepseek/deepseek-v4.1-flash", "google/gemini-3.8-flash", "openai/gpt-4.1-mini"]
RUNS = 3


def load_cases() -> list[dict]:
    cases = json.load(open("Problem_doc/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))["cases"]
    cases += json.load(open("bench/paraphrases.json"))["cases"]          # same {input, expected_output} shape
    return cases


def note_matches(got: dict, exp: dict) -> bool:
    if got["directive_type"] != exp["directive_type"] or got["applies"] != exp["applies"]:
        return False
    g, e = got["structured_adjustment"], exp["structured_adjustment"]
    if e is None:
        return g is None
    if g is None or g["hours"] != e["hours"]:
        return False
    return all(abs(g[k] - v) <= 0.01 for k, v in e.items() if k != "hours")


async def bench(model: str, cases: list[dict]) -> dict:
    graph = build_graph(llm=make_chat(model))
    lat, hits, total, failures = [], 0, 0, 0

    async def one(case):
        nonlocal hits, total, failures
        t0 = time.perf_counter()
        try:
            out = await graph.ainvoke(GraphState(request=OptimizeRequest(**case["input"])))
        except Exception as e:
            failures += 1
            print(f"  {model} {case['id']}: FAIL {type(e).__name__}", file=sys.stderr)
            return
        lat.append(time.perf_counter() - t0)
        failures += bool(out.get("problems"))
        for got, exp in zip(out["directives"], case["expected_output"]["directive_interpretation"]):
            total += 1
            hits += note_matches(got.model_dump(), exp)

    for _ in range(RUNS):
        for case in cases:
            await one(case)
    await asyncio.gather(*(one(c) for c in cases[:10]))          # burst of 10 for the semaphore/429 check

    lat.sort()
    return dict(model=model, accuracy=hits / max(total, 1), p50=statistics.median(lat),
                p95=lat[int(0.95 * (len(lat) - 1))], failures=failures, n=len(lat))


async def main():
    cases = load_cases()
    rows = [await bench(m, cases) for m in MODELS]
    rows.sort(key=lambda r: (-r["accuracy"], r["p95"]))
    print("| model | accuracy | p50 s | p95 s | failures | n |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['model']} | {r['accuracy']:.3f} | {r['p50']:.2f} | {r['p95']:.2f} | {r['failures']} | {r['n']} |")
    ok = [r for r in rows if r["p95"] <= 5 and r["failures"] == 0] or rows
    print(f"\nLLM_MODEL={ok[0]['model']}")
    print("LLM_FALLBACK_MODELS=" + ",".join(r["model"] for r in rows if r is not ok[0]))


if __name__ == "__main__":
    asyncio.run(main())
```

The bench reuses the production graph, so what it measures is exactly what the judge will hit,
including guardrails, LP and replay. It never touches the fallback chain (each model is tested
alone), which is what makes the per-model numbers honest.

---

## 7. `app/guardrails.py` — deterministic validator/normaliser

```python
import math
from .schemas import (OptimizeRequest, LLMInterpretation, LLMDirective, DirectiveInterpretation,
                      SolarReductionAdjustment, ReserveAdjustment, WindowAdjustment, GridCapAdjustment, TimeWindow)


class GuardrailError(ValueError):
    pass


def expand_windows(windows: list[TimeWindow]) -> list[int]:
    hours: set[int] = set()
    for w in windows:
        s, e = w.start_hour, w.end_hour
        if s == e:
            continue                      # zero-length window carries no hours
        if e > s:
            hours.update(range(s, e))
        else:                             # overnight wrap, e.g. 22 -> 2  => 22,23,0,1
            hours.update(range(s, 24))
            hours.update(range(0, e))
    return sorted(h for h in hours if 0 <= h <= 23)


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def no_op(i: int, why: str) -> DirectiveInterpretation:
    return DirectiveInterpretation(note_index=i, applies=False, directive_type="no_op",
                                   structured_adjustment=None, explanation=why)


def to_directive(d: LLMDirective, capacity: float) -> DirectiveInterpretation:
    t = d.directive_type
    if t == "no_op":
        return no_op(d.note_index, d.explanation or "This note does not affect today's energy schedule.")

    hours = expand_windows(d.windows)
    if not hours:
        raise GuardrailError("no valid hours were given for a non-no_op directive")

    if t == "solar_reduction":
        f = d.solar_remaining_fraction
        if not _finite(f):
            raise GuardrailError("solar_remaining_fraction missing")
        if 1.0 < f <= 100.0:              # model answered in percent
            f = f / 100.0
        adj = SolarReductionAdjustment(hours=hours, factor=round(min(max(f, 0.0), 1.0), 4))
    elif t == "minimum_battery_reserve":
        v = d.minimum_energy_kwh
        if not _finite(v) and _finite(d.minimum_energy_percent_of_capacity):
            v = capacity * d.minimum_energy_percent_of_capacity / 100.0
        if not _finite(v):
            raise GuardrailError("minimum_energy_kwh missing")
        adj = ReserveAdjustment(hours=hours, minimum_energy_kwh=round(min(max(v, 0.0), capacity), 4))
    elif t in ("no_charge_window", "no_discharge_window"):
        adj = WindowAdjustment(hours=hours)
    elif t == "max_grid_window":
        v = d.max_grid_kwh
        if not _finite(v) or v < 0:
            raise GuardrailError("max_grid_kwh missing or negative")
        adj = GridCapAdjustment(hours=hours, max_grid_kwh=round(v, 4))
    else:                                 # unreachable thanks to Literal, kept for safety
        raise GuardrailError(f"unsupported directive_type {t!r}")

    return DirectiveInterpretation(note_index=d.note_index, applies=True, directive_type=t,
                                   structured_adjustment=adj, explanation=d.explanation or t.replace("_", " "))


def normalize(raw: LLMInterpretation | None, req: OptimizeRequest) -> tuple[list[DirectiveInterpretation], list[str]]:
    """Returns (one entry per note in order, list of problems). Problems non-empty => caller may retry."""
    n = len(req.operator_notes)
    problems: list[str] = []
    by_index: dict[int, LLMDirective] = {}
    for d in (raw.directives if raw else []):
        if not 0 <= d.note_index < n:
            problems.append(f"note_index {d.note_index} does not exist (valid: 0..{n-1})")
        elif d.note_index in by_index:
            problems.append(f"duplicate entry for note_index {d.note_index}")
        else:
            by_index[d.note_index] = d

    out: list[DirectiveInterpretation] = []
    for i in range(n):
        d = by_index.get(i)
        if d is None:
            problems.append(f"missing entry for note_index {i}")
            out.append(no_op(i, "Interpretation unavailable; treated as no_op."))
            continue
        try:
            out.append(to_directive(d, req.battery.capacity_kwh))
        except GuardrailError as e:
            problems.append(f"note_index {i}: {e}")
            out.append(no_op(i, f"Interpretation rejected by guardrails ({e}); treated as no_op."))
    return out, problems
```

Guardrail checklist coverage: type ∈ 6 (Literal + pydantic), note mapping complete/unique, hours
unique/ascending/0–23 (expansion), factor ∈ [0,1] (clamp), reserve finite/≥0/≤capacity (clamp),
grid cap finite/≥0, applies semantics (constructor), no invented base parameters (the LLM has no
field to change them). Final replay is Section 9.

---

## 8. `app/optimizer.py` — LP with HiGHS

### 8.1 Directive → constraint arrays

```python
from dataclasses import dataclass
import numpy as np
from scipy.optimize import linprog
from .schemas import OptimizeRequest, DirectiveInterpretation, HourPlan

H = 24
EPS = 1e-6      # tiny penalty on charge+discharge: prevents simultaneous charge/discharge and pointless cycling
TOL = 1e-7


class InfeasibleScenario(Exception):
    pass


@dataclass
class Constraints:
    eff_solar: list[float]
    floor: list[float]          # min battery_energy_after per hour
    can_charge: list[bool]
    can_discharge: list[bool]
    grid_cap: list[float]       # math.inf when uncapped


def build_constraints(req: OptimizeRequest, directives: list[DirectiveInterpretation]) -> Constraints:
    b = req.battery
    c = Constraints(
        eff_solar=[h.solar_kwh for h in req.hours],
        floor=[b.minimum_energy_kwh] * H,
        can_charge=[True] * H,
        can_discharge=[True] * H,
        grid_cap=[float("inf")] * H,
    )
    for d in directives:
        if not d.applies:
            continue
        a = d.structured_adjustment
        for h in a.hours:
            if d.directive_type == "solar_reduction":
                c.eff_solar[h] *= a.factor          # product: satisfies both "min" and "product" readings if two overlap
            elif d.directive_type == "minimum_battery_reserve":
                c.floor[h] = max(c.floor[h], a.minimum_energy_kwh)
            elif d.directive_type == "no_charge_window":
                c.can_charge[h] = False
            elif d.directive_type == "no_discharge_window":
                c.can_discharge[h] = False
            elif d.directive_type == "max_grid_window":
                c.grid_cap[h] = min(c.grid_cap[h], a.max_grid_kwh)
    return c
```

### 8.2 LP formulation

Variables per hour h (index blocks of 24): `g` grid import, `s` solar used, `c` charge, `d` discharge,
`E` energy after hour h.

- minimise `Σ tariff[h]·g[h] + EPS·Σ(c[h]+d[h])`
- balance: `g[h] + s[h] + d[h] − c[h] = demand[h]`
- energy: `E[h] − E[h−1] − c[h] + d[h] = 0`, with `E[−1] = initial`
- bounds: `0 ≤ g ≤ grid_cap`, `0 ≤ s ≤ eff_solar`, `0 ≤ c ≤ max_charge` (0 in no-charge hours),
  `0 ≤ d ≤ max_discharge` (0 in no-discharge hours), `floor[h] ≤ E[h] ≤ capacity`, and `E[23] = initial`.

```python
def solve(req: OptimizeRequest, cons: Constraints) -> list[HourPlan]:
    b = req.battery
    G, S, C, D, E = (np.arange(H) + k * H for k in range(5))
    n = 5 * H
    demand = np.array([h.demand_kwh for h in req.hours])
    tariff = np.array([h.tariff_bdt_per_kwh for h in req.hours])

    cost = np.zeros(n)
    cost[G] = tariff
    cost[C] = EPS
    cost[D] = EPS

    A_eq = np.zeros((2 * H, n))
    b_eq = np.zeros(2 * H)
    for h in range(H):
        A_eq[h, [G[h], S[h], D[h], C[h]]] = [1, 1, 1, -1]
        b_eq[h] = demand[h]
        r = H + h
        A_eq[r, [E[h], C[h], D[h]]] = [1, -1, 1]
        if h == 0:
            b_eq[r] = b.initial_energy_kwh
        else:
            A_eq[r, E[h - 1]] = -1

    lb = np.zeros(n)
    ub = np.full(n, np.inf)
    for h in range(H):
        ub[G[h]] = cons.grid_cap[h]
        ub[S[h]] = cons.eff_solar[h]
        ub[C[h]] = b.max_charge_kwh_per_hour if cons.can_charge[h] else 0.0
        ub[D[h]] = b.max_discharge_kwh_per_hour if cons.can_discharge[h] else 0.0
        lb[E[h]] = cons.floor[h]
        ub[E[h]] = b.capacity_kwh
    lb[E[H - 1]] = max(lb[E[H - 1]], b.initial_energy_kwh)   # end-of-day neutrality
    ub[E[H - 1]] = b.initial_energy_kwh
    if np.any(lb > ub + TOL):
        raise InfeasibleScenario("reserve directive conflicts with battery limits")

    res = linprog(cost, A_eq=A_eq, b_eq=b_eq, bounds=list(zip(lb, ub)), method="highs")
    if res.status != 0:
        raise InfeasibleScenario(res.message)
    return extract_plan(req, cons, res.x, G, S, C, D)
```

### 8.3 Plan extraction (netting + rounding that keeps balance exact)

```python
def extract_plan(req, cons, x, G, S, C, D) -> list[HourPlan]:
    energy = req.battery.initial_energy_kwh
    plan: list[HourPlan] = []
    for h in range(H):
        net = x[C[h]] - x[D[h]]
        if net > TOL:
            action, amt = "charge", net
        elif net < -TOL:
            action, amt = "discharge", -net
        else:
            action, amt = "idle", 0.0
        amt = round(amt, 4)
        solar = round(min(max(x[S[h]], 0.0), cons.eff_solar[h]), 4)
        charge = amt if action == "charge" else 0.0
        discharge = amt if action == "discharge" else 0.0
        grid = req.hours[h].demand_kwh + charge - discharge - solar   # residual => balance holds exactly
        if grid < 0:                                                  # only rounding can cause this
            solar = round(solar + grid, 4)
            grid = 0.0
        energy = round(energy + charge - discharge, 4)
        plan.append(HourPlan(hour=h, grid_kwh=round(grid, 4), solar_used_kwh=solar,
                             battery_action=action, battery_kwh=amt, battery_energy_after_kwh=energy))
    return plan
```

Rounding to 4 decimals keeps every judge check inside the ±0.01 tolerance (worst case ≈ 24 × 5e-5).

**Verified while planning:** the `build_constraints` / `solve` / `extract_plan` / `replay` bodies above were run
against all 10 public samples using the expected interpretations. Every case reproduced the reference
`total_cost_bdt` exactly, passed `replay`, and the organizer's reference plans also pass `replay`.

---

## 9. `app/validator.py` — final replay + totals

```python
import math
from .optimizer import build_constraints
from .schemas import OptimizeRequest, DirectiveInterpretation, HourPlan

REPLAY_TOL = 1e-3   # stricter than the judge's 0.01


def replay(req: OptimizeRequest, directives: list[DirectiveInterpretation], plan: list[HourPlan]) -> list[str]:
    cons = build_constraints(req, directives)
    b = req.battery
    errs: list[str] = []
    if [p.hour for p in plan] != list(range(24)):
        return ["hourly_plan must contain hours 0..23 in order"]
    energy = b.initial_energy_kwh
    for p, hin in zip(plan, req.hours):
        h = p.hour
        for name in ("grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh"):
            v = getattr(p, name)
            if not math.isfinite(v) or v < -REPLAY_TOL:
                errs.append(f"h{h}: {name} not finite/non-negative")
        charge = p.battery_kwh if p.battery_action == "charge" else 0.0
        discharge = p.battery_kwh if p.battery_action == "discharge" else 0.0
        if p.battery_action == "idle" and abs(p.battery_kwh) > REPLAY_TOL:
            errs.append(f"h{h}: idle with non-zero battery_kwh")
        if charge > b.max_charge_kwh_per_hour + REPLAY_TOL or discharge > b.max_discharge_kwh_per_hour + REPLAY_TOL:
            errs.append(f"h{h}: rate limit exceeded")
        if charge > REPLAY_TOL and not cons.can_charge[h]:
            errs.append(f"h{h}: charged inside no_charge_window")
        if discharge > REPLAY_TOL and not cons.can_discharge[h]:
            errs.append(f"h{h}: discharged inside no_discharge_window")
        if p.solar_used_kwh > cons.eff_solar[h] + REPLAY_TOL:
            errs.append(f"h{h}: solar_used exceeds effective solar")
        if p.grid_kwh > cons.grid_cap[h] + REPLAY_TOL:
            errs.append(f"h{h}: grid exceeds max_grid_window")
        if abs(p.grid_kwh + p.solar_used_kwh + discharge - hin.demand_kwh - charge) > REPLAY_TOL:
            errs.append(f"h{h}: energy balance violated")
        energy += charge - discharge
        if abs(energy - p.battery_energy_after_kwh) > REPLAY_TOL:
            errs.append(f"h{h}: battery_energy_after_kwh inconsistent")
        if energy < cons.floor[h] - REPLAY_TOL or energy > b.capacity_kwh + REPLAY_TOL:
            errs.append(f"h{h}: battery bounds violated")
    if abs(energy - b.initial_energy_kwh) > REPLAY_TOL:
        errs.append("end-of-day battery energy != initial")
    return errs


def totals(req: OptimizeRequest, plan: list[HourPlan]) -> tuple[float, float, float]:
    grid = [p.grid_kwh for p in plan]
    cost = sum(p.grid_kwh * h.tariff_bdt_per_kwh for p, h in zip(plan, req.hours))
    return round(sum(grid), 4), round(cost, 4), round(max(grid), 4)
```

`replay` is reused verbatim by `scripts/run_samples.py` and by the team's tests.

---

## 10. `app/graph.py` and `app/main.py`

### 10.1 Graph

```
START → interpret → guardrail ──(problems & attempts < max)──► interpret
                        │
                        └──(clean, or attempts exhausted)──► optimize → validate → summarize → END
```

```python
import asyncio, logging
from langgraph.graph import StateGraph, START, END
from .config import settings
from .state import GraphState
from .llm import build_llm, build_messages
from .guardrails import normalize
from .optimizer import build_constraints, solve
from .validator import replay, totals
from .summary import summarize

log = logging.getLogger("gridwise")


def make_interpret_node(llm):
    sem = asyncio.Semaphore(settings.LLM_CONCURRENCY)

    async def interpret(state: GraphState) -> dict:
        messages = build_messages(state.request, state.problems)
        try:
            async with sem:
                raw = await asyncio.wait_for(llm.ainvoke(messages), timeout=settings.LLM_TIMEOUT_S)
            return {"raw": raw, "attempts": state.attempts + 1, "llm_error": None}
        except Exception as e:                      # timeout, provider error, schema parse failure
            log.warning("LLM attempt %d failed: %s", state.attempts + 1, type(e).__name__)
            return {"raw": None, "attempts": state.attempts + 1, "llm_error": type(e).__name__}
    return interpret


def guardrail_node(state: GraphState) -> dict:
    directives, problems = normalize(state.raw, state.request)
    if state.llm_error:
        problems = [f"LLM call failed ({state.llm_error})"] + problems
    return {"directives": directives, "problems": problems}


def route_after_guardrail(state: GraphState) -> str:
    if state.problems and state.attempts < settings.LLM_MAX_ATTEMPTS:
        return "retry"
    return "proceed"


def optimize_node(state: GraphState) -> dict:
    cons = build_constraints(state.request, state.directives)
    plan = solve(state.request, cons)                 # raises InfeasibleScenario → 422 in main.py
    warnings = list(state.warnings)
    if state.problems:
        warnings.append("degraded interpretation: " + "; ".join(state.problems))
    return {"plan": plan, "warnings": warnings}


def validate_node(state: GraphState) -> dict:
    errs = replay(state.request, state.directives, state.plan)
    if errs:
        raise RuntimeError("final validation failed: " + "; ".join(errs[:5]))   # → controlled 500
    g, c, p = totals(state.request, state.plan)
    return {"total_grid_kwh": g, "total_cost_bdt": c, "peak_grid_kwh": p}


def summarize_node(state: GraphState) -> dict:
    return {"plan_summary": summarize(state)}


def build_graph(llm=None):
    """`llm` is injectable so tests can pass a FakeLLM; production passes nothing and gets ChatOpenRouter."""
    llm = llm if llm is not None else build_llm()
    g = StateGraph(GraphState)
    g.add_node("interpret", make_interpret_node(llm))
    g.add_node("guardrail", guardrail_node)
    g.add_node("optimize", optimize_node)
    g.add_node("validate", validate_node)
    g.add_node("summarize", summarize_node)
    g.add_edge(START, "interpret")
    g.add_edge("interpret", "guardrail")
    g.add_conditional_edges("guardrail", route_after_guardrail, {"retry": "interpret", "proceed": "optimize"})
    g.add_edge("optimize", "validate")
    g.add_edge("validate", "summarize")
    g.add_edge("summarize", END)
    return g.compile()
```

### 10.2 `app/summary.py`

```python
def summarize(state) -> str:
    applied = [d for d in state.directives if d.applies]
    ignored = len(state.directives) - len(applied)
    charged = sum(p.battery_kwh for p in state.plan if p.battery_action == "charge")
    parts = [f"Applied {len(applied)} directive(s)" + (f" ({', '.join(d.directive_type for d in applied)})" if applied else "")]
    if ignored:
        parts.append(f"ignored {ignored} unrelated note(s)")
    parts.append(f"shifted {charged:.1f} kWh through the battery from cheaper to costlier hours")
    parts.append(f"restored the battery to {state.request.battery.initial_energy_kwh:g} kWh at end of day")
    parts.append(f"total grid cost {state.total_cost_bdt:.2f} BDT")
    return "; ".join(parts) + "."
```

### 10.3 `app/main.py`

```python
import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .schemas import OptimizeRequest, OptimizeResponse
from .state import GraphState
from .graph import build_graph
from .llm import LLMNotConfigured
from .optimizer import InfeasibleScenario

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gridwise")

app = FastAPI(title="GridWise LLM")
_graph = None


def get_graph():
    """Lazy: import and /health never touch the LLM client or the API key."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


@app.exception_handler(RequestValidationError)
async def _bad_request(_: Request, exc: RequestValidationError):
    # Covers invalid JSON bodies and schema violations → 400 as required by the contract.
    # Verified on FastAPI 0.141: unparseable JSON, empty body, wrong content-type, JSON array and
    # schema violations all arrive here, so no extra handler is needed.
    errors = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg", "")} for e in exc.errors()]
    return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": errors})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(req: OptimizeRequest):
    try:
        out = await get_graph().ainvoke(GraphState(request=req))
    except LLMNotConfigured:
        log.error("LLM not configured")
        return JSONResponse(status_code=500, content={"error": "llm_not_configured"})
    except InfeasibleScenario as e:
        return JSONResponse(status_code=422, content={"error": "infeasible_scenario", "detail": str(e)})
    except Exception:
        log.exception("optimize-energy failed for scenario %s", req.scenario_id)
        return JSONResponse(status_code=500, content={"error": "internal_error"})
    for w in out.get("warnings", []):
        log.warning("%s: %s", req.scenario_id, w)
    return OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=out["directives"],
        hourly_plan=out["plan"],
        total_grid_kwh=out["total_grid_kwh"],
        total_cost_bdt=out["total_cost_bdt"],
        peak_grid_kwh=out["peak_grid_kwh"],
        plan_summary=out["plan_summary"],
    )
```

Run: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`.

Secret hygiene: the only place the key exists is the environment; logs record exception *class names*
only; 500 bodies are constant; `.env` is git-ignored; `.env.example` has names only.

---

## 11. Deployment, Docker, README

**Dockerfile**

```dockerfile
FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
```

Build/push: `docker build -t <dockerhub-user>/gridwise-llm:v1 . && docker push ...`; record the
digest from the push output in README. Run command for judges:
`docker run --rm -p 8000:8000 -e OPENROUTER_API_KEY=... <image>@sha256:<digest>`.

**Hosting**: any provider that runs the container with a public URL and does not sleep. Start the container
**without** a key once to confirm `/health` still answers; that is what the Docker fallback check exercises. Free tiers
that spin down (Render free, Fly auto-stop) risk the 60 s health-readiness and 30 s request checks;
prefer a small always-on instance (Railway / Render starter / Fly with `min_machines_running=1` / a
VPS). Bind `0.0.0.0`; read `PORT` from env because most PaaS inject it.

**README must contain** (rubric §7 checks these literally): quickstart from a clean clone (`uv sync` or
`pip install .`, `.env` from `.env.example`, run command), env var names, model/provider
(OpenRouter + model ids), LLM's role (interpretation only), guardrail list, optimizer (LP, SciPy
HiGHS), curl for `/health` and one sample `/optimize-energy`, `python scripts/run_samples.py`
procedure, Docker pull/run with tag+digest, known limitations, secret-handling guidance, and a
**credits section** naming every external tool used:

- LangGraph, LangChain core, `langchain-openrouter` (OpenRouter's LangChain integration)
- OpenRouter (hosted LLM gateway); the three candidate model ids (`deepseek/deepseek-v4.1-flash`,
  `google/gemini-3.8-flash`, `openai/gpt-4.1-mini`), the bake-off table from `scripts/bench_models.py`,
  and which one is primary vs fallback
- FastAPI, Uvicorn, Pydantic, pydantic-settings
- SciPy (HiGHS LP solver), NumPy
- Docker + registry (Docker Hub/GHCR), hosting provider
- AI coding assistant (Claude Code) used for scaffolding; architecture and logic are the team's own
- Any web docs consulted (the three links at the top of this plan)

---

## 12. Build order and time budget (4 h window)

| Slot | Work | Done when |
|---|---|---|
| 0:00–0:15 | New private repo, `pyproject`, layout, `config.py`, `.env.example`, `/health` up | curl health returns `{"status":"ok"}` |
| 0:15–0:45 | `schemas.py`, `optimizer.py`, `validator.py` | A script that skips the LLM and feeds the sample `expected_output.directive_interpretation` into the optimizer reproduces every sample's `total_cost_bdt` within 0.01 and passes `replay` |
| 0:45–1:30 | `llm.py`, `guardrails.py`, `state.py`, `graph.py`, `main.py` | All 10 samples via HTTP produce the expected interpretations |
| 1:30–1:55 | Model bake-off (Section 6.2): write `bench/paraphrases.json` (10 notes), run `scripts/bench_models.py` over the 3 candidates, set `LLM_MODEL` / `LLM_FALLBACK_MODELS`, paste the table into README | A winner with accuracy 1.0 and p95 < 5 s; other two ordered as fallbacks |
| 1:55–2:20 | Dockerfile, build, push, run from the image, deploy, test both endpoints from outside | Public URL passes samples |
| 2:20–2:50 | README (Section 11), credits, digest, curl examples | Teammate can follow it cold |
| 2:50–3:30 | Test phase (Section 13), fix findings | Team-written tests green |
| 3:30–3:50 | 3-minute video, final external re-check of the live URL | Submitted |
| 3:50–4:00 | Buffer | — |

---

## 13. Test phase (team writes the cases; the harness is ours)

Tooling the implementation ships for the test phase:

- `scripts/run_samples.py`: POSTs every `cases[i].input` from the public JSON to a base URL,
  asserts `directive_interpretation` equals the expected `directive_type`/`structured_adjustment`
  per note (explanation text ignored), runs `validator.replay`, and prints
  `team_cost / expected_cost` per case.
- `tests/conftest.py`: a `FakeLLM` whose `ainvoke` returns a canned `LLMInterpretation` (or raises, to
  simulate provider failure), injected via `build_graph(llm=...)` so pipeline tests run offline.
- `bench/paraphrases.json` (10 team-written notes, Section 6.2) is shared by `scripts/bench_models.py`
  and `scripts/run_samples.py`, so paraphrase robustness is checked from the first run.
- `scripts/bench_models.py` can be re-run in the test phase against any model id to re-check the
  choice after prompt changes; if the prompt changes, re-run it before redeploying.
- `validator.replay` importable as the single source of truth for "is this plan valid".

Categories the team's test cases should cover (each maps to a rubric line):

1. **Interpretation**: each of the 6 types; paraphrases (24-hour times, "noon", "midnight",
   "one-fifth", "half", "80% reduction" vs "drops to 20%", "at or below", percent-of-capacity reserve);
   overnight windows; distractors that mention batteries/solar but state no constraint; 1, 2 and 3 notes.
2. **Guardrails** (with `FakeLLM`): missing note, duplicate note, out-of-range index, zero-length
   window, factor > 1, factor given as percent, negative cap, reserve above capacity, `no_op` with a
   non-null adjustment, LLM exception/timeout → degraded `no_op` and 200.
3. **Optimizer**: every directive is visibly applied (grid ≤ cap in window, no charge in window, etc.);
   cost within 0.01 of every sample; neutrality; no simultaneous charge/discharge; tariff-zero hours;
   solar > demand hours (curtailment); reserve equal to capacity at hour 23 edge.
4. **API contract**: `/health` with **no API key configured**; malformed JSON → 400; 23 hours → 400;
   duplicate hour → 400; 4 notes → 400; empty note → 400; extra fields ignored; totals recomputed from
   plan; scenario_id echoed; order of `directive_interpretation`; missing key → controlled 500 on POST only.
5. **Reliability**: 20 concurrent valid requests, p95 < 5 s, zero failures; response bodies never
   contain the key or stack traces.

Exit criteria for the phase: all categories green locally **and** against the deployed URL.
