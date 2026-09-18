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
