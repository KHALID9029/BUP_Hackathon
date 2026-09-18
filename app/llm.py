import json

from langchain_openrouter import ChatOpenRouter
from langchain_core.messages import SystemMessage, HumanMessage

from .config import settings
from .schemas import LLMInterpretation, OptimizeRequest


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

# Structured-output method per model. Default is "json_schema" (strict, schema-enforced).
# Verified while testing: OpenAI's strict json_schema mode rejects this schema
# ("additionalProperties is required to be supplied and to be false" on nested/array items),
# so gpt-4.1-mini uses tool-call based "function_calling" instead.
STRUCTURED_METHOD = {
    "openai/gpt-4.1-mini": "function_calling",
}


def make_chat(model: str):
    """One structured-output chat model for a given OpenRouter id. Used by build_llm() and by the bench script."""
    kwargs = dict(
        model=model,
        api_key=settings.OPENROUTER_API_KEY,
        temperature=0,
        max_tokens=settings.LLM_MAX_TOKENS,
        max_retries=1,
        # The node wraps the call in asyncio.wait_for(settings.LLM_TIMEOUT_S) instead of relying on this client's
        # own timeout semantics.
    )
    if REASONING.get(model):
        kwargs["reasoning"] = REASONING[model]
    chat = ChatOpenRouter(**kwargs)
    method = STRUCTURED_METHOD.get(model, "json_schema")
    return chat.with_structured_output(LLMInterpretation, method=method)


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
