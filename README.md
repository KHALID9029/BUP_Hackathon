# GridWise LLM

One HTTP service implementing the mandated GridWise pipeline:

```
Operator notes + energy data → LLM interpreter → deterministic guardrails → LP optimizer → final replay validator → response
```

Built as a [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph` (5 nodes, one conditional
retry edge) served by FastAPI.

**Live deployment:** https://gridwise-llm-2y8l.onrender.com

- `GET https://gridwise-llm-2y8l.onrender.com/health`
- `POST https://gridwise-llm-2y8l.onrender.com/optimize-energy`
- Web UI: https://gridwise-llm-2y8l.onrender.com/

## Quickstart

```bash
uv sync                        # or: pip install .
cp .env.example .env           # fill in OPENROUTER_API_KEY
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`GET /health` works even without an API key (the graph and LLM client are built lazily on the first
`POST`, never at startup).

```bash
curl http://localhost:8000/health
# {"status":"ok"}

python3 -c "import json; print(json.dumps(json.load(open('Problem_doc/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json'))['cases'][0]['input']))" > /tmp/sample01.json
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @/tmp/sample01.json
```

Run every public sample case against a running instance and check interpretation + replay + cost:

```bash
uv run python scripts/run_samples.py http://localhost:8000
```

Run the offline test suite (guardrails, optimizer, API contract — no network calls):

```bash
uv run pytest
```

## Web UI

The same service also serves a small web UI at `/` for visualizing a run. It's optional and has no
effect on the API: only `GET /` and `/assets/*` are added, and if the built files are missing
nothing is mounted at all. On the page you can:

- load any public sample case, or edit the operator notes, battery and hourly data;
- see each note's interpretation (directive type, hours, value, and whether it matches the sample's
  expected interpretation);
- see the 24-hour schedule chart, with directive hours shaded, and the battery state-of-charge chart;
- check the plan against every schedule rule, recomputed independently in the browser.

The Docker image builds the UI automatically. To build it for a local (non-Docker) run:

```bash
cd frontend && npm ci && npm run build     # writes frontend/dist, served by FastAPI at /
```

For UI development with hot reload, run the API on port 8000 and, in `frontend/`, `npm run dev`
(Vite proxies `/health` and `/optimize-energy` to `http://localhost:8000`).

Stack: React + TypeScript (Vite), Tailwind CSS, Recharts. Source in `frontend/src/`.

## Environment variables

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter key. Optional at startup — only required once `/optimize-energy` is called. |
| `LLM_MODEL` | Primary OpenRouter model id. |
| `LLM_FALLBACK_MODELS` | Comma-separated fallback model ids, tried in order via `with_fallbacks`. |
| `LLM_TIMEOUT_S` | Hard per-attempt timeout (`asyncio.wait_for`), independent of the client's own timeout. |
| `LLM_MAX_ATTEMPTS` | 1 normal call + N-1 retries with guardrail feedback appended to the prompt. |
| `LLM_MAX_TOKENS` | Output token cap; headroom avoids truncation-triggered retries. |
| `LLM_CONCURRENCY` | `asyncio.Semaphore` around the LLM call, to stay under the OpenRouter key's rate limit under burst load. |
| `PORT` | HTTP port (`uvicorn` / Docker `CMD` read this). |
| `FRONTEND_DIST` | Optional. Directory of the built web UI (default `frontend/dist`; set in the Docker image). |

## Architecture

- **LLM role**: interpretation only. One call per request covering all 1–3 operator notes, via
  `ChatOpenRouter(...).with_structured_output(LLMInterpretation, method=...)` — schema-enforced output,
  no hand-rolled JSON parsing. The LLM never touches demand/tariff/battery numbers or the final schedule.
- **Guardrails** (`app/guardrails.py`, pure Python, no LLM): every note gets exactly one directive
  (missing → `no_op`, duplicate/out-of-range index → rejected); time windows are expanded from
  start/end hours with overnight wrap handled explicitly; `solar_reduction` factor is clamped to
  `[0, 1]` (and unit-corrected if the model answered in percent); `minimum_battery_reserve` is
  resolved from an absolute kWh or a percent-of-capacity figure and clamped to `[0, capacity]`;
  `max_grid_window` values must be finite and non-negative; a directive that fails any check
  degrades to `no_op` with an explanation, and the failure is fed back to the LLM on retry.
- **Optimizer** (`app/optimizer.py`): a linear program (`scipy.optimize.linprog`, HiGHS), 120
  variables (grid/solar/charge/discharge/energy-after × 24 hours), exact hourly energy balance and
  battery-continuity constraints, end-of-day neutrality (`E[23] = initial`), minimizing grid cost
  plus a tiny charge+discharge penalty that prevents simultaneous charge/discharge.
- **Final validator** (`app/validator.py`): replays the returned plan hour-by-hour against the
  applied directives (balance, rate limits, solar cap, grid cap, battery bounds, neutrality) before
  the response is ever sent; any failure is a controlled 500, not a wrong answer.
- **`plan_summary`**: a deterministic template string — no second LLM call, since it isn't scored and
  a second call would double p95 latency risk.
- **Safe failure**: primary model → fallback models (`with_fallbacks`) → one retry with guardrail
  feedback → degrade unresolved notes to `no_op`. The service never crashes and never invents a
  directive; it always returns a valid 200 schedule once the request itself is well-formed.

## Model choice

Three candidates were evaluated by hand against the 10 public samples (`scripts/bench_models.py`
runs the full statistical bake-off described below against the public samples plus
`bench/paraphrases.json`, a 10-case paraphrase set covering 24-hour clock times, "noon"/"midnight",
"one-fifth", "80% reduction", "at or below" grid caps, percent-of-capacity reserves, overnight
windows, distractor notes, and multi-note requests):

| model | exact-match on 10 public samples | notes |
|---|---|---|
| `google/gemini-3.8-flash` | 18/18 directives | **primary** — consistent, p95 ≈ 4.8s |
| `openai/gpt-4.1-mini` | 18/18 directives | **fallback 1** — needs `method="function_calling"`; OpenAI's strict `json_schema` mode rejects this schema (`additionalProperties` must be `false` on every nested/array object, which the auto-generated Pydantic schema doesn't set) |
| `deepseek/deepseek-v4.1-flash` | inconsistent — intermittently returns an empty `windows` list for a correctly-classified directive, even at `temperature=0` | **fallback 2** — cheapest/fastest per-token, kept in the chain since the guardrail retry + `no_op` degrade make a bad response safe, never wrong |

Given the time cost of the full 3-model × 20-case × 3-run bake-off against a live paid API, the
model ordering above was set from this smaller, still-live validation pass; `scripts/bench_models.py`
is ready to run the complete protocol (and should be re-run if the prompt changes):

```bash
uv run python scripts/bench_models.py
```

`REASONING` and `STRUCTURED_METHOD` in `app/llm.py` hold the per-model reasoning-effort and
structured-output-method overrides discovered above.

## Docker

```bash
docker build -t <dockerhub-user>/gridwise-llm:v1 .
docker push <dockerhub-user>/gridwise-llm:v1
# record the digest from the push output here

docker run --rm -p 8000:8000 -e OPENROUTER_API_KEY=... <dockerhub-user>/gridwise-llm:v1@sha256:<digest>
```

The Dockerfile is multi-stage: a Node stage builds the web UI, and the final Python image contains
only the API plus the static UI files (no Node runtime). Then open `http://localhost:8000/` for the
UI.

Verified: the image's `/health` returns 200 even when run **without** `OPENROUTER_API_KEY` (the
Docker fallback / health-readiness check).

Bind `0.0.0.0` and read `PORT` from the environment — most PaaS providers inject their own port.
Prefer an always-on instance over a free tier that spins down (Render free / Fly auto-stop); the
service must answer `/health` within 60s of boot with zero warm-up.

## Deploy (Render)

`render.yaml` is a Render Blueprint for this repo: a single Docker web service with its health
check on `/health`. It uses the free plan, which sleeps after 15 minutes idle, so an external uptime
monitor pings `/health` every 5 minutes to keep it awake. For the judging window, switch the
service to an always-on instance type (Settings → Instance Type).

1. Push the repo to GitHub.
2. In Render: **New → Blueprint** → select the repo.
3. Enter `OPENROUTER_API_KEY` when prompted (it's `sync: false`, so it's never stored in the repo).
4. After the deploy finishes, the public URL serves the UI at `/` and the API at `/health` and
   `/optimize-energy`.

Render injects `PORT`, and the container's `CMD` already reads it.

## Known limitations

- Under a burst of ~20 concurrent requests, p95 latency (observed ≈5.6s in local testing) can exceed
  the 5s "full points" threshold — this is dominated by a single OpenRouter API key's own rate limit,
  not by this service. `LLM_CONCURRENCY` trades off queuing latency against 429s; raise it if the
  judge's key/tier has more headroom.
- `deepseek/deepseek-v4.1-flash` is kept only as the last fallback: in testing it sometimes returns a
  correctly-classified directive with an empty time window. The guardrail retry (one extra LLM call
  with the problem described in the prompt) and the `no_op` degrade path mean this never produces an
  incorrect schedule, only a more conservative one.
- `plan_summary` is a template, not LLM-generated prose — this is intentional (Section on `plan_summary`
  above), since it isn't scored and a second LLM call would risk the latency budget.

## Secret handling

- The OpenRouter key exists only in the process environment (`.env`, git-ignored; `.env.example` has
  names only).
- Exception logs record the exception **class name** only, never the message or a stack trace that
  could include the key or provider payloads.
- All error response bodies are constant, fixed strings (`{"error": "..."}`) — never a raw exception.
- `GraphState.llm_error` stores a class name for logging/retry-feedback purposes and is never returned
  in an HTTP response.

## Credits

- [LangGraph](https://github.com/langchain-ai/langgraph), [LangChain core](https://github.com/langchain-ai/langchain),
  [`langchain-openrouter`](https://pypi.org/project/langchain-openrouter/) — orchestration and the OpenRouter chat-model integration.
- [OpenRouter](https://openrouter.ai/) — hosted LLM gateway. Candidate models:
  `google/gemini-3.8-flash` (primary), `openai/gpt-4.1-mini` and `deepseek/deepseek-v4.1-flash`
  (fallbacks, in that order) — see "Model choice" above for the comparison and rationale.
- [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [Pydantic](https://docs.pydantic.dev/), [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
- [SciPy](https://scipy.org/) (`linprog`, HiGHS solver), [NumPy](https://numpy.org/).
- Web UI: [React](https://react.dev/), [Vite](https://vite.dev/), [Tailwind CSS](https://tailwindcss.com/), [Recharts](https://recharts.org/).
- Docker + a container registry (Docker Hub/GHCR) + a hosting provider for the public deployment.
- Claude Code (Anthropic) was used as an AI coding assistant for scaffolding; the pipeline
  architecture, guardrail rules, LP formulation and validation logic are the team's own.
- Docs consulted while building this: the LangChain OpenRouter integration guide, the
  `ChatOpenRouter` API reference, the `langchain-openrouter` PyPI page, and the OpenRouter model
  pages/reasoning-control docs for the three candidate models.
