from app.guardrails import normalize, expand_windows, to_directive
from app.schemas import LLMInterpretation, LLMDirective, TimeWindow, OptimizeRequest, HourInput, BatterySpec


def make_request(notes: list[str]) -> OptimizeRequest:
    hours = [HourInput(hour=h, demand_kwh=100, solar_kwh=0, tariff_bdt_per_kwh=5) for h in range(24)]
    battery = BatterySpec(capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=40,
                           max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)
    return OptimizeRequest(scenario_id="T", operator_notes=notes, hours=hours, battery=battery)


def test_expand_windows_overnight_wrap():
    assert expand_windows([TimeWindow(start_hour=22, end_hour=2)]) == [0, 1, 22, 23]


def test_expand_windows_zero_length():
    assert expand_windows([TimeWindow(start_hour=5, end_hour=5)]) == []


def test_missing_note_becomes_no_op_with_problem():
    req = make_request(["note a", "note b"])
    raw = LLMInterpretation(directives=[
        LLMDirective(note_index=0, directive_type="no_op", explanation="x"),
    ])
    out, problems = normalize(raw, req)
    assert len(out) == 2
    assert out[1].directive_type == "no_op"
    assert any("missing entry for note_index 1" in p for p in problems)


def test_duplicate_note_index_reported():
    req = make_request(["note a"])
    raw = LLMInterpretation(directives=[
        LLMDirective(note_index=0, directive_type="no_op", explanation="x"),
        LLMDirective(note_index=0, directive_type="no_op", explanation="y"),
    ])
    _, problems = normalize(raw, req)
    assert any("duplicate entry" in p for p in problems)


def test_out_of_range_index_reported():
    req = make_request(["note a"])
    raw = LLMInterpretation(directives=[
        LLMDirective(note_index=5, directive_type="no_op", explanation="x"),
    ])
    _, problems = normalize(raw, req)
    assert any("does not exist" in p for p in problems)


def test_solar_reduction_factor_given_as_percent():
    d = LLMDirective(note_index=0, directive_type="solar_reduction",
                      windows=[TimeWindow(start_hour=1, end_hour=2)],
                      solar_remaining_fraction=25, explanation="x")
    interp = to_directive(d, capacity=200)
    assert interp.structured_adjustment.factor == 0.25


def test_solar_reduction_factor_clamped_above_one():
    d = LLMDirective(note_index=0, directive_type="solar_reduction",
                      windows=[TimeWindow(start_hour=1, end_hour=2)],
                      solar_remaining_fraction=150, explanation="x")
    interp = to_directive(d, capacity=200)
    assert interp.structured_adjustment.factor == 1.0


def test_reserve_percent_of_capacity_converted():
    d = LLMDirective(note_index=0, directive_type="minimum_battery_reserve",
                      windows=[TimeWindow(start_hour=1, end_hour=2)],
                      minimum_energy_percent_of_capacity=50, explanation="x")
    interp = to_directive(d, capacity=200)
    assert interp.structured_adjustment.minimum_energy_kwh == 100.0


def test_reserve_above_capacity_clamped():
    d = LLMDirective(note_index=0, directive_type="minimum_battery_reserve",
                      windows=[TimeWindow(start_hour=1, end_hour=2)],
                      minimum_energy_kwh=999, explanation="x")
    interp = to_directive(d, capacity=200)
    assert interp.structured_adjustment.minimum_energy_kwh == 200.0


def test_zero_length_window_rejected_for_non_no_op():
    req = make_request(["note a"])
    raw = LLMInterpretation(directives=[
        LLMDirective(note_index=0, directive_type="no_charge_window",
                     windows=[TimeWindow(start_hour=5, end_hour=5)], explanation="x"),
    ])
    out, problems = normalize(raw, req)
    assert out[0].directive_type == "no_op"
    assert any("no valid hours" in p for p in problems)


def test_negative_grid_cap_rejected():
    req = make_request(["note a"])
    raw = LLMInterpretation(directives=[
        LLMDirective(note_index=0, directive_type="max_grid_window",
                     windows=[TimeWindow(start_hour=1, end_hour=2)], max_grid_kwh=-5, explanation="x"),
    ])
    out, problems = normalize(raw, req)
    assert out[0].directive_type == "no_op"
    assert problems


def test_llm_none_degrades_all_notes_to_no_op():
    req = make_request(["note a", "note b"])
    out, problems = normalize(None, req)
    assert all(d.directive_type == "no_op" and not d.applies for d in out)
    assert len(problems) == 2
