import math

from .optimizer import build_constraints
from .schemas import OptimizeRequest, DirectiveInterpretation, HourPlan

REPLAY_TOL = 1e-3   # stricter than the judge's 0.01


def replay(req: OptimizeRequest, directives: list[DirectiveInterpretation], plan: list[HourPlan]) -> list[str]:
    cons = build_constraints(req, directives)
    b = req.battery
    errs: list[str] = []
    if [p.hour for p in plan] != list(range(24)):
        return ["hourly_plan must contain hours 0..23 in order"]
    energy = b.initial_energy_kwh
    for p, hin in zip(plan, req.hours):
        h = p.hour
        for name in ("grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh"):
            v = getattr(p, name)
            if not math.isfinite(v) or v < -REPLAY_TOL:
                errs.append(f"h{h}: {name} not finite/non-negative")
        charge = p.battery_kwh if p.battery_action == "charge" else 0.0
        discharge = p.battery_kwh if p.battery_action == "discharge" else 0.0
        if p.battery_action == "idle" and abs(p.battery_kwh) > REPLAY_TOL:
            errs.append(f"h{h}: idle with non-zero battery_kwh")
        if charge > b.max_charge_kwh_per_hour + REPLAY_TOL or discharge > b.max_discharge_kwh_per_hour + REPLAY_TOL:
            errs.append(f"h{h}: rate limit exceeded")
        if charge > REPLAY_TOL and not cons.can_charge[h]:
            errs.append(f"h{h}: charged inside no_charge_window")
        if discharge > REPLAY_TOL and not cons.can_discharge[h]:
            errs.append(f"h{h}: discharged inside no_discharge_window")
        if p.solar_used_kwh > cons.eff_solar[h] + REPLAY_TOL:
            errs.append(f"h{h}: solar_used exceeds effective solar")
        if p.grid_kwh > cons.grid_cap[h] + REPLAY_TOL:
            errs.append(f"h{h}: grid exceeds max_grid_window")
        if abs(p.grid_kwh + p.solar_used_kwh + discharge - hin.demand_kwh - charge) > REPLAY_TOL:
            errs.append(f"h{h}: energy balance violated")
        energy += charge - discharge
        if abs(energy - p.battery_energy_after_kwh) > REPLAY_TOL:
            errs.append(f"h{h}: battery_energy_after_kwh inconsistent")
        if energy < cons.floor[h] - REPLAY_TOL or energy > b.capacity_kwh + REPLAY_TOL:
            errs.append(f"h{h}: battery bounds violated")
    if abs(energy - b.initial_energy_kwh) > REPLAY_TOL:
        errs.append("end-of-day battery energy != initial")
    return errs


def totals(req: OptimizeRequest, plan: list[HourPlan]) -> tuple[float, float, float]:
    grid = [p.grid_kwh for p in plan]
    cost = sum(p.grid_kwh * h.tariff_bdt_per_kwh for p, h in zip(plan, req.hours))
    return round(sum(grid), 4), round(cost, 4), round(max(grid), 4)
