"""Model bake-off: run every candidate OpenRouter model through the production graph over the public
samples + bench/paraphrases.json, score interpretation accuracy and latency, and print the
LLM_MODEL / LLM_FALLBACK_MODELS values to put in .env.

Usage: python scripts/bench_models.py
"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph import build_graph
from app.llm import make_chat
from app.schemas import OptimizeRequest
from app.state import GraphState

ROOT = Path(__file__).resolve().parent.parent
MODELS = ["deepseek/deepseek-v4.1-flash", "google/gemini-3.8-flash", "openai/gpt-4.1-mini"]
RUNS = 3


def load_cases() -> list[dict]:
    cases = json.loads((ROOT / "Problem_doc" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text())["cases"]
    cases += json.loads((ROOT / "bench" / "paraphrases.json").read_text())["cases"]
    return cases


def note_matches(got: dict, exp: dict) -> bool:
    if got["directive_type"] != exp["directive_type"] or got["applies"] != exp["applies"]:
        return False
    g, e = got.get("structured_adjustment"), exp.get("structured_adjustment")
    if e is None:
        return g is None
    if g is None or g["hours"] != e["hours"]:
        return False
    return all(abs(g[k] - v) <= 0.01 for k, v in e.items() if k != "hours")


async def bench(model: str, cases: list[dict]) -> dict:
    graph = build_graph(llm=make_chat(model))
    lat: list[float] = []
    hits = total = failures = 0

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
        if out.get("problems"):
            failures += 1
        got_list = [d.model_dump() for d in out["directives"]]
        for got, exp in zip(got_list, case["expected_output"]["directive_interpretation"]):
            total += 1
            hits += note_matches(got, exp)

    for _ in range(RUNS):
        for case in cases:
            await one(case)
    await asyncio.gather(*(one(c) for c in cases[:10]))          # burst of 10 for the semaphore/429 check

    lat.sort()
    p50 = statistics.median(lat) if lat else float("inf")
    p95 = lat[int(0.95 * (len(lat) - 1))] if lat else float("inf")
    return dict(model=model, accuracy=hits / max(total, 1), p50=p50, p95=p95, failures=failures, n=len(lat))


async def main():
    cases = load_cases()
    rows = [await bench(m, cases) for m in MODELS]
    rows.sort(key=lambda r: (-r["accuracy"], r["p95"]))
    print("\n| model | accuracy | p50 s | p95 s | failures | n |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['model']} | {r['accuracy']:.3f} | {r['p50']:.2f} | {r['p95']:.2f} | {r['failures']} | {r['n']} |")
    ok = [r for r in rows if r["p95"] <= 5 and r["failures"] == 0] or rows
    print(f"\nLLM_MODEL={ok[0]['model']}")
    print("LLM_FALLBACK_MODELS=" + ",".join(r["model"] for r in rows if r is not ok[0]))


if __name__ == "__main__":
    asyncio.run(main())
