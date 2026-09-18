import asyncio
import logging

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
