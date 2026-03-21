from app.state import BriefingState


def route_after_analyzer(state: BriefingState) -> str:
    """
    Decide what to do after the Analyzer node runs.

    Returns the name of the next node:
    - "find_alternates"  if destination is unusable
    - "no_go_briefing"   if fuel is already insufficient
    - "critic"           if everything looks good
    """
    # Destination unusable — need to find alternates before proceeding
    if state.get("destination_is_unusable"):
        reason = state.get("reason_unusable", "unknown reason")
        print(f"  [Router] Destination unusable: {reason}")
        print(f"  [Router] → find_alternates")
        return "find_alternates"

    # Fuel already insufficient — no point continuing
    fuel = state.get("fuel_analysis", "")
    if fuel and "FUEL INSUFFICIENT" in fuel:
        print("  [Router] Fuel insufficient → no_go_briefing")
        return "no_go_briefing"

    # All clear — proceed to critic review
    print("  [Router] Conditions acceptable → critic")
    return "critic"


def route_after_alternates(state: BriefingState) -> str:
    """
    Decide what to do after alternate airports have been evaluated.

    Returns:
    - "no_go_briefing"  if no viable alternates were found
    - "critic"          if a viable alternate exists
    """
    alternates = state.get("alternates", "")

    if not alternates:
        print("  [Router] No alternates found → no_go_briefing")
        return "no_go_briefing"

    if "No viable alternates" in alternates or \
       "WARNING: No suitable alternates" in alternates:
        print("  [Router] No suitable alternates → no_go_briefing")
        return "no_go_briefing"

    print("  [Router] Alternate found → critic")
    return "critic"