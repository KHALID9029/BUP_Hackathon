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
