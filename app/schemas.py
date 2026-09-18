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
