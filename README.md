# GridWise LLM

GridWise LLM is one HTTP service for the BUP CSE Fest 2026 preliminary. It takes a 24-hour campus
energy scenario and 1–3 free-text operator notes, and returns a structured interpretation of each
note plus a valid 24-hour battery/grid schedule with the lowest possible grid cost.

```
Operator notes + energy data → LLM interpreter → deterministic guardrails → LP optimizer → final replay validator → response
```

The pipeline is a [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph` (5 nodes, plus
one conditional edge that retries the LLM once), served by FastAPI. The same service also serves a
small web UI for visualizing runs.

## Submission at a glance

| Item | Value |
|---|---|
| Live API base URL | **https://gridwise-llm-2y8l.onrender.com** |
| Health check | `GET https://gridwise-llm-2y8l.onrender.com/health` → `{"status":"ok"}` |
| Main endpoint | `POST https://gridwise-llm-2y8l.onrender.com/optimize-energy` |
| Web UI (optional, not part of the API) | https://gridwise-llm-2y8l.onrender.com/ |
| Docker image (pin by digest) | `kha9029/gridwise-llm@sha256:b145c8f6c9bd9b19157aa29a44f308b094c1d10c9a5fb86399902cfdadf89d63` |
| Docker image (tag) | `kha9029/gridwise-llm:v2` |
| Container port | `8000` (override with `PORT`), binds `0.0.0.0` |
| LLM provider | OpenRouter; primary `google/gemini-3.8-flash`, fallbacks `openai/gpt-4.1-mini` → `deepseek/deepseek-v4.1-flash` |
| Optimizer | Linear program, SciPy `linprog` with the HiGHS solver |
| API reference | [`api.md`](api.md) |

---

## 1. Quickstart: run the Docker image (fastest)

This needs only Docker. The image is public, so no login is required.

```bash
docker run --rm -p 8000:8000 \
  -e OPENROUTER_API_KEY=<your-openrouter-key> \
  kha9029/gridwise-llm@sha256:b145c8f6c9bd9b19157aa29a44f308b094c1d10c9a5fb86399902cfdadf89d63
```

- `/health` is ready within a few seconds of the container starting, and works **even without**
  `OPENROUTER_API_KEY`. Without a key, only `POST /optimize-energy` fails, with a controlled
  `500 {"error":"llm_not_configured"}`.
- To use a different port, add `-e PORT=9000 -p 9000:9000`.
- Instead of `-e`, you can pass the key with `--env-file .env` (see Section 3). Never put the
  key in the image itself.

Then run the checks in Section 4.

## 2. Quickstart: run from source

Requirements: **Python 3.12+**, and **Node.js 20+** (only needed to build the optional web UI).

```bash
git clone https://github.com/KHALID9029/BUP_Hackathon.git
cd BUP_Hackathon

# Python environment. Either use uv:
uv sync
# ...or plain pip (Windows: .venv\Scripts\activate):
python -m venv .venv && source .venv/bin/activate
pip install -e . httpx pytest pytest-asyncio

# Configuration: copy the template, then set OPENROUTER_API_KEY in .env
cp .env.example .env

# Optional: build the web UI (served at /). The API works without it.
cd frontend && npm ci && npm run build && cd ..

# Run the service
uvicorn app.main:app --host 0.0.0.0 --port 8000
# (with uv: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000)
```

## 3. Environment variables

`.env.example` lists every variable. It contains no secret values. The service reads variables
from the process environment, or from `.env` in the working directory.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENROUTER_API_KEY` | For `POST /optimize-energy` | — | OpenRouter API key. Not needed at startup or for `/health`. |
| `LLM_MODEL` | No | `google/gemini-3.8-flash` | Primary OpenRouter model id. |
| `LLM_FALLBACK_MODELS` | No | `openai/gpt-4.1-mini,deepseek/deepseek-v4.1-flash` | Comma-separated fallback model ids, tried in order. |
| `LLM_TIMEOUT_S` | No | `10.0` | Hard timeout per LLM attempt (`asyncio.wait_for`). |
| `LLM_MAX_ATTEMPTS` | No | `2` | 1 normal call + 1 retry with the guardrail problems fed back into the prompt. |
| `LLM_MAX_TOKENS` | No | `1000` | Output token cap. |
| `LLM_CONCURRENCY` | No | `20` | Maximum concurrent LLM calls (semaphore), to stay under the key's rate limit. |
| `PORT` | No | `8000` | HTTP port. |
| `FRONTEND_DIST` | No | `frontend/dist` | Directory of the built web UI. If it's missing, the UI is simply not mounted. |

## 4. Testing

### 4.1 Health and one sample request

```bash
curl http://localhost:8000/health
# {"status":"ok"}

python -c "import json; print(json.dumps(json.load(open('Problem_doc/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json'))['cases'][0]['input']))" > sample01.json
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @sample01.json
```

To test the live deployment, replace `http://localhost:8000` with
`https://gridwise-llm-2y8l.onrender.com`.

### 4.2 All 10 public sample cases

`scripts/run_samples.py` sends every public sample to a running instance. For each case it:

- checks each note's `directive_interpretation` against the expected type, hours and values
  (the explanation text is ignored);
- replays the returned `hourly_plan` hour by hour against every rule (energy balance, battery
  bounds and rates, effective solar, directive windows, reserve, grid cap, end-of-day neutrality);
- compares `total_cost_bdt` with the organizer's reference cost.

```bash
python scripts/run_samples.py http://localhost:8000
python scripts/run_samples.py https://gridwise-llm-2y8l.onrender.com
```

Expected result: `10/10 cases passed`, every case `ratio=1.0000`. Verified against the live
deployment and against the Docker image above.

### 4.3 Offline test suite (no network, no API key)

A fake LLM is injected, so these tests make no real calls. They cover the guardrails, the
optimizer (all 10 samples reproduce the reference cost exactly), and the API contract (400
cases, controlled 500s, no key leakage, path handling, `HEAD /health`).

```bash
pytest          # or: uv run pytest     → 40 passed
```

### 4.4 Paraphrase robustness and model comparison

`bench/paraphrases.json` holds 10 extra hand-written cases. They cover 24-hour clock times,
"noon"/"midnight", "one-fifth", "80% reduction", "at or below" grid caps, percent-of-capacity
reserves, overnight windows, distractor notes and 3-note requests.
`scripts/bench_models.py` runs these plus the public samples against each candidate model, and
reports accuracy and latency. It makes real, paid LLM calls.

```bash
python scripts/bench_models.py
```

### 4.5 Web UI

Open `http://localhost:8000/` (or the live URL). Pick a public sample, or edit the notes and
battery values, then click **Run optimization**. The page shows:

- each note's interpretation, and whether it matches the sample's expected answer;
- the 24-hour energy chart, with each directive's hours shaded;
- the battery state-of-charge chart against the minimum floor and any reserve;
- the result of an independent in-browser replay of every rule.

## 5. Architecture

| Stage | File | What it does |
|---|---|---|
| **LLM interpreter** | `app/llm.py` | **One** OpenRouter call per request covers all 1–3 notes, via `ChatOpenRouter(...).with_structured_output(LLMInterpretation)`, so output is schema-enforced. The LLM returns a directive type, time windows as start/end hours, and the numeric value for each note. **The LLM's only role is interpretation**: it never sees or changes demand, tariff or the schedule, and it never computes hour lists. |
| **Guardrails** | `app/guardrails.py` | Plain deterministic Python, no LLM. See Section 6. |
| **Optimizer** | `app/optimizer.py` | A linear program solved with SciPy `linprog` (HiGHS). 120 variables: grid, solar used, charge, discharge and energy-after for each of 24 hours. Minimizes Σ grid × tariff, subject to hourly energy balance, battery continuity and bounds, rate limits, effective solar, all directive constraints, and `E[23] = initial`. A tiny penalty on charge + discharge rules out simultaneous charging and discharging. |
| **Final validator** | `app/validator.py` | Replays the plan hour by hour against every rule and every applied directive before responding. `total_grid_kwh`, `total_cost_bdt` and `peak_grid_kwh` are **recomputed from `hourly_plan`**, not taken from the solver. If replay fails, the service returns a controlled 500 rather than an invalid plan. |
| **Summary** | `app/summary.py` | `plan_summary` is a deterministic template. There is no second LLM call. |
| **API** | `app/main.py` | FastAPI app. `GET /health`, `POST /optimize-energy`, the error mapping, and the optional UI at `/`. |
| **Graph** | `app/graph.py` | `interpret → guardrail → (retry interpret once if problems) → optimize → validate → summarize`. |

HTTP behavior:

- `200` on success.
- `400 {"error":"invalid_request","detail":[...]}` for malformed JSON or any schema violation:
  not exactly 24 hours, duplicate hours, 0 or more than 3 notes, blank notes, negative values,
  or initial energy outside [min, capacity].
- `422 {"error":"infeasible_scenario"}` when no valid schedule exists.
- `500` with a fixed body otherwise. `HEAD /health` is supported for uptime monitors, and
  double or trailing slashes (e.g. `//optimize-energy`) are normalized.

**Safe failure:** the service tries the primary model, then the fallback models, then one retry
with the guardrail problems added to the prompt. Any note still unresolved becomes `no_op`, with an
explanation. The service never crashes and never invents a directive; a well-formed request always
gets a valid schedule.

## 6. Guardrails

Every LLM output is checked before it can affect the schedule:

- Each note gets **exactly one** entry, in `note_index` order. A missing note becomes `no_op`;
  duplicate or out-of-range indexes are rejected.
- `directive_type` must be one of the 6 allowed values, enforced by both the schema and the code.
- Hours come from start/end windows (start inclusive, end exclusive) and are expanded **in code**,
  so overnight windows wrap correctly (`23 → 4` becomes `[0,1,2,3,23]`). The result is always
  unique, ascending and within 0–23. A real directive with no valid hours is rejected.
- `solar_reduction.factor` is the fraction that *remains*. It is clamped to [0, 1], and
  corrected if the model answered in percent.
- `minimum_battery_reserve` accepts an absolute kWh value or a percentage of capacity. The
  conversion uses the request's own capacity, never the model's arithmetic, and the result is
  clamped to [0, capacity].
- `max_grid_window.max_grid_kwh` must be finite and non-negative.
- `applies` is `false` if and only if the type is `no_op`, and `structured_adjustment` is
  `null` if and only if the type is `no_op`.
- The LLM output schema has no fields for demand, tariff or battery parameters, so the model
  cannot change them.
- A directive that fails any check becomes `no_op` with an explanation, and the problem is sent
  back to the LLM on the retry.

## 7. Model choice

Three candidates on three different providers were run live against the 10 public samples:

| Model | Result on the 10 public samples | Role |
|---|---|---|
| `google/gemini-3.8-flash` | 18/18 directives exact | **Primary**: consistent, lowest reasoning setting (`effort: low`) |
| `openai/gpt-4.1-mini` | 18/18 directives exact | **Fallback 1**: uses `method="function_calling"`, because OpenAI's strict `json_schema` mode rejects the auto-generated Pydantic schema |
| `deepseek/deepseek-v4.1-flash` | Inconsistent: sometimes returns an empty time window for a correctly classified directive | **Fallback 2**: the guardrail retry and the `no_op` fallback make a bad answer safe rather than wrong |

The primary also passed all 10 paraphrase cases in `bench/paraphrases.json`. The per-model
reasoning and structured-output settings are in `REASONING` and `STRUCTURED_METHOD` in
`app/llm.py`.

## 8. Docker

```bash
# Pull and run the published image (see Section 1)
docker pull kha9029/gridwise-llm@sha256:b145c8f6c9bd9b19157aa29a44f308b094c1d10c9a5fb86399902cfdadf89d63

# Or build it yourself from this repo
docker build -t gridwise-llm .
docker run --rm -p 8000:8000 -e OPENROUTER_API_KEY=<your-openrouter-key> gridwise-llm
```

- The build has two stages. A Node stage builds the web UI; the final image is `python:3.12-slim`
  with the API and the static UI files, with no Node runtime.
- `.dockerignore` excludes `.env`, `.git` and local environments, so **no secrets are baked into
  the image**. This was confirmed by inspecting the published image.
- The container binds `0.0.0.0` and reads `PORT`, defaulting to 8000.
- The published image was verified by pulling it by digest without logging in. Without a key,
  `/health` returns 200. With a key, all 10 public samples pass.

## 9. Deployment

The live service runs on **Render** as a single Docker web service, built from this repo's
`Dockerfile` using the Blueprint in `render.yaml`. `OPENROUTER_API_KEY` is set in the Render
dashboard (`sync: false`), never in the repo. The health check path is `/health`.

To reproduce: in Render, choose **New → Blueprint**, select this repo, and enter
`OPENROUTER_API_KEY` when prompted.

## 10. Known limitations

- **Latency depends on the LLM.** Every request makes one live OpenRouter call. In testing, a
  single request took 2.7–5.8 s end to end. With 20 simultaneous requests on one API key, p95
  was about 9.4 s (0 failures). Nearly all of this is the model and gateway; the LP solve and
  replay take milliseconds. `LLM_CONCURRENCY` trades queueing delay against rate-limit errors.
- **Hosted LLM dependency.** If OpenRouter or all three models are unavailable, notes are
  treated as `no_op` and a valid (but less constrained) schedule is still returned. A missing
  key gives `500 llm_not_configured` on `POST` only.
- **Infeasible scenarios** (for example, a reserve the battery can't physically reach) return
  `422`. Organizer-valid scenarios are always feasible.
- `plan_summary` is template text, not LLM-written, by design.

## 11. Secret handling

- The OpenRouter key exists only in the process environment: in `.env` locally (git-ignored),
  as `-e` / `--env-file` for Docker, or as a dashboard variable on Render. `.env.example`
  contains names and non-secret defaults only.
- The key is never logged. Logs record only the exception **class name**, never the message or a
  stack trace that could contain provider payloads.
- Error responses have fixed bodies (`{"error": "..."}`), never raw exceptions or stack traces.
  The tests assert that the key never appears in any response.
- The key is never sent to the LLM. Prompts contain only the notes and the battery parameters.

## 12. Repository layout

```
app/           FastAPI app, LangGraph pipeline, guardrails, optimizer, validator
frontend/      Web UI (React + TypeScript + Vite + Tailwind + Recharts)
scripts/       run_samples.py (public-sample checker), bench_models.py (model comparison)
bench/         paraphrases.json (10 extra paraphrase cases)
tests/         Offline pytest suite (fake LLM)
Problem_doc/   Official problem docs and public sample cases
Dockerfile, render.yaml, .env.example, pyproject.toml, api.md
```

## 13. Credits

- [LangGraph](https://github.com/langchain-ai/langgraph), [LangChain core](https://github.com/langchain-ai/langchain),
  [`langchain-openrouter`](https://pypi.org/project/langchain-openrouter/): pipeline orchestration and the OpenRouter chat-model integration.
- [OpenRouter](https://openrouter.ai/): hosted LLM gateway. Models: `google/gemini-3.8-flash`,
  `openai/gpt-4.1-mini`, `deepseek/deepseek-v4.1-flash` (see Section 7).
- [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [Pydantic](https://docs.pydantic.dev/), [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
- [SciPy](https://scipy.org/) (`linprog`, HiGHS solver), [NumPy](https://numpy.org/).
- Web UI: [React](https://react.dev/), [Vite](https://vite.dev/), [Tailwind CSS](https://tailwindcss.com/), [Recharts](https://recharts.org/).
- [Docker](https://www.docker.com/) and [Docker Hub](https://hub.docker.com/) for the image; [Render](https://render.com/) for hosting; [UptimeRobot](https://uptimerobot.com/) for health monitoring.
- Claude Code (Anthropic) was used as an AI coding assistant for scaffolding. The pipeline
  architecture, guardrail rules, LP formulation and validation logic are the team's own.
- Docs consulted: the LangChain OpenRouter integration guide, the `ChatOpenRouter` API reference,
  the `langchain-openrouter` PyPI page, and the OpenRouter model pages and reasoning-control docs.
