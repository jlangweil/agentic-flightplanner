from app.state import BriefingState
from app.tools.alternates import suggest_alternates_tool


def find_alternates_node(state: BriefingState) -> dict:
    """
    Find viable alternate airports when the destination is unusable.
    Stores results in state for the Critic and Briefing nodes to use.
    """
    destination = state["destination_icao"]
    reason = state.get("reason_unusable", "Destination unusable")

    print(f"  [FindAlternates] Searching near {destination}...")

    result = suggest_alternates_tool.invoke({
        "destination_icao": destination,
        "reason": reason,
        "radius_nm": 75,
        "min_runway_ft": 3000,
    })

    print(f"  [FindAlternates] Result:\n{result}")

    # Also recalculate fuel to the best alternate if we have aircraft params
    fuel_to_alternate = ""
    if all([
        state.get("fuel_onboard_gal"),
        state.get("fuel_burn_gph"),
        state.get("true_airspeed_kts"),
    ]):
        best_icao = _extract_best_alternate(result)
        if best_icao:
            from app.nodes.analyzer import _estimate_distance
            from app.tools.fuel import calculate_fuel_tool

            dist = _estimate_distance(state["departure_icao"], best_icao)
            if dist:
                fuel_to_alternate = calculate_fuel_tool.invoke({
                    "distance_nm": dist,
                    "fuel_onboard_gal": state["fuel_onboard_gal"],
                    "fuel_burn_gph": state["fuel_burn_gph"],
                    "true_airspeed_kts": state["true_airspeed_kts"],
                    "is_ifr": state.get("is_ifr") or False,
                    "is_night": state.get("is_night") or False,
                    "alternate_distance_nm": 0,
                })
                print(f"  [FindAlternates] Fuel to alternate:\n{fuel_to_alternate}")

    return {
        "alternates": result,
        "fuel_analysis": fuel_to_alternate or state.get("fuel_analysis", ""),
    }


def _extract_best_alternate(alternates_text: str) -> str | None:
    """Pull the best alternate ICAO code from the tool output."""
    for line in alternates_text.splitlines():
        if "Best alternate:" in line:
            # Line looks like: "  Best alternate: KUUU (Newport State Airport)"
            parts = line.split("Best alternate:")
            if len(parts) > 1:
                icao = parts[1].strip().split()[0]
                if len(icao) == 4:
                    return icao
    return None