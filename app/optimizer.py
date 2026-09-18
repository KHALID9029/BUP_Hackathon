from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from .schemas import OptimizeRequest, DirectiveInterpretation, HourPlan

H = 24
EPS = 1e-6      # tiny penalty on charge+discharge: prevents simultaneous charge/discharge and pointless cycling
TOL = 1e-7


class InfeasibleScenario(Exception):
    pass


@dataclass
class Constraints:
    eff_solar: list[float]
    floor: list[float]          # min battery_energy_after per hour
    can_charge: list[bool]
    can_discharge: list[bool]
    grid_cap: list[float]       # math.inf when uncapped


def build_constraints(req: OptimizeRequest, directives: list[DirectiveInterpretation]) -> Constraints:
    b = req.battery
    c = Constraints(
        eff_solar=[h.solar_kwh for h in req.hours],
        floor=[b.minimum_energy_kwh] * H,
        can_charge=[True] * H,
        can_discharge=[True] * H,
        grid_cap=[float("inf")] * H,
    )
    for d in directives:
        if not d.applies:
            continue
        a = d.structured_adjustment
        for h in a.hours:
            if d.directive_type == "solar_reduction":
                c.eff_solar[h] *= a.factor          # product: satisfies both "min" and "product" readings if two overlap
            elif d.directive_type == "minimum_battery_reserve":
                c.floor[h] = max(c.floor[h], a.minimum_energy_kwh)
            elif d.directive_type == "no_charge_window":
                c.can_charge[h] = False
            elif d.directive_type == "no_discharge_window":
                c.can_discharge[h] = False
            elif d.directive_type == "max_grid_window":
                c.grid_cap[h] = min(c.grid_cap[h], a.max_grid_kwh)
    return c


def solve(req: OptimizeRequest, cons: Constraints) -> list[HourPlan]:
    b = req.battery
    G, S, C, D, E = (np.arange(H) + k * H for k in range(5))
    n = 5 * H
    demand = np.array([h.demand_kwh for h in req.hours])
    tariff = np.array([h.tariff_bdt_per_kwh for h in req.hours])

    cost = np.zeros(n)
    cost[G] = tariff
    cost[C] = EPS
    cost[D] = EPS

    A_eq = np.zeros((2 * H, n))
    b_eq = np.zeros(2 * H)
    for h in range(H):
        A_eq[h, [G[h], S[h], D[h], C[h]]] = [1, 1, 1, -1]
        b_eq[h] = demand[h]
        r = H + h
        A_eq[r, [E[h], C[h], D[h]]] = [1, -1, 1]
        if h == 0:
            b_eq[r] = b.initial_energy_kwh
        else:
            A_eq[r, E[h - 1]] = -1

    lb = np.zeros(n)
    ub = np.full(n, np.inf)
    for h in range(H):
        ub[G[h]] = cons.grid_cap[h]
        ub[S[h]] = cons.eff_solar[h]
        ub[C[h]] = b.max_charge_kwh_per_hour if cons.can_charge[h] else 0.0
        ub[D[h]] = b.max_discharge_kwh_per_hour if cons.can_discharge[h] else 0.0
        lb[E[h]] = cons.floor[h]
        ub[E[h]] = b.capacity_kwh
    lb[E[H - 1]] = max(lb[E[H - 1]], b.initial_energy_kwh)   # end-of-day neutrality
    ub[E[H - 1]] = b.initial_energy_kwh
    if np.any(lb > ub + TOL):
        raise InfeasibleScenario("reserve directive conflicts with battery limits")

    res = linprog(cost, A_eq=A_eq, b_eq=b_eq, bounds=list(zip(lb, ub)), method="highs")
    if res.status != 0:
        raise InfeasibleScenario(res.message)
    return extract_plan(req, cons, res.x, G, S, C, D)


def extract_plan(req, cons, x, G, S, C, D) -> list[HourPlan]:
    energy = req.battery.initial_energy_kwh
    plan: list[HourPlan] = []
    for h in range(H):
        net = x[C[h]] - x[D[h]]
        if net > TOL:
            action, amt = "charge", net
        elif net < -TOL:
            action, amt = "discharge", -net
        else:
            action, amt = "idle", 0.0
        amt = round(amt, 4)
        solar = round(min(max(x[S[h]], 0.0), cons.eff_solar[h]), 4)
        charge = amt if action == "charge" else 0.0
        discharge = amt if action == "discharge" else 0.0
        grid = req.hours[h].demand_kwh + charge - discharge - solar   # residual => balance holds exactly
        if grid < 0:                                                  # only rounding can cause this
            solar = round(solar + grid, 4)
            grid = 0.0
        energy = round(energy + charge - discharge, 4)
        plan.append(HourPlan(hour=h, grid_kwh=round(grid, 4), solar_used_kwh=solar,
                              battery_action=action, battery_kwh=amt, battery_energy_after_kwh=energy))
    return plan
