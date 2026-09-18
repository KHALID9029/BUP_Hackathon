def summarize(state) -> str:
    applied = [d for d in state.directives if d.applies]
    ignored = len(state.directives) - len(applied)
    charged = sum(p.battery_kwh for p in state.plan if p.battery_action == "charge")
    parts = [f"Applied {len(applied)} directive(s)" + (f" ({', '.join(d.directive_type for d in applied)})" if applied else "")]
    if ignored:
        parts.append(f"ignored {ignored} unrelated note(s)")
    parts.append(f"shifted {charged:.1f} kWh through the battery from cheaper to costlier hours")
    parts.append(f"restored the battery to {state.request.battery.initial_energy_kwh:g} kWh at end of day")
    parts.append(f"total grid cost {state.total_cost_bdt:.2f} BDT")
    return "; ".join(parts) + "."
