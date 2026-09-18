import json
from pathlib import Path

import pytest

from app.schemas import OptimizeRequest, DirectiveInterpretation, HourInput, BatterySpec
from app.optimizer import build_constraints, solve, InfeasibleScenario
from app.validator import replay, totals

SAMPLES = Path(__file__).resolve().parent.parent / "Problem_doc" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def load_cases():
    return json.loads(SAMPLES.read_text())["cases"]


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["id"])
def test_public_sample_reproduces_reference_cost_and_passes_replay(case):
    req = OptimizeRequest(**case["input"])
    expected = case["expected_output"]
    directives = [DirectiveInterpretation(**d) for d in expected["directive_interpretation"]]

    cons = build_constraints(req, directives)
    plan = solve(req, cons)
    errs = replay(req, directives, plan)
    assert errs == []

    _, cost, _ = totals(req, plan)
    assert cost == pytest.approx(expected["total_cost_bdt"], abs=0.01)


def make_request(**overrides) -> OptimizeRequest:
    hours = [HourInput(hour=h, demand_kwh=100, solar_kwh=0, tariff_bdt_per_kwh=5) for h in range(24)]
    battery = BatterySpec(capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=40,
                           max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)
    return OptimizeRequest(scenario_id="T", operator_notes=["n"], hours=hours, battery=battery)


def test_end_of_day_neutrality():
    req = make_request()
    plan = solve(req, build_constraints(req, []))
    assert plan[-1].battery_energy_after_kwh == pytest.approx(req.battery.initial_energy_kwh, abs=1e-3)


def test_no_simultaneous_charge_and_discharge():
    req = make_request()
    plan = solve(req, build_constraints(req, []))
    for p in plan:
        if p.battery_action == "idle":
            assert p.battery_kwh == 0.0
        else:
            assert p.battery_kwh >= 0.0


def test_infeasible_reserve_conflicts_with_capacity():
    hours = [HourInput(hour=h, demand_kwh=100, solar_kwh=0, tariff_bdt_per_kwh=5) for h in range(24)]
    battery = BatterySpec(capacity_kwh=100, initial_energy_kwh=100, minimum_energy_kwh=40,
                           max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)
    req = OptimizeRequest(scenario_id="T", operator_notes=["n"], hours=hours, battery=battery)
    directive = DirectiveInterpretation(
        note_index=0, applies=True, directive_type="minimum_battery_reserve",
        structured_adjustment={"hours": [0], "minimum_energy_kwh": 999}, explanation="x",
    )
    with pytest.raises(InfeasibleScenario):
        solve(req, build_constraints(req, [directive]))


def test_solar_curtailment_when_solar_exceeds_demand():
    hours = [HourInput(hour=h, demand_kwh=10, solar_kwh=500, tariff_bdt_per_kwh=5) for h in range(24)]
    battery = BatterySpec(capacity_kwh=100, initial_energy_kwh=50, minimum_energy_kwh=10,
                           max_charge_kwh_per_hour=20, max_discharge_kwh_per_hour=20)
    req = OptimizeRequest(scenario_id="T", operator_notes=["n"], hours=hours, battery=battery)
    plan = solve(req, build_constraints(req, []))
    for p in plan:
        assert p.solar_used_kwh <= 30 + 1e-6   # demand(10) + max_charge(20) is the most solar can be used
