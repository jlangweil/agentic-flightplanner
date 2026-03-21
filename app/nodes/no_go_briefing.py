from app.state import BriefingState


def no_go_briefing_node(state: BriefingState) -> dict:
    """
    Generate a NO-GO briefing when conditions are clearly unacceptable.
    Called when destination is unusable AND no alternates were found,
    or when fuel is insufficient.
    """
    departure = state["departure_icao"]
    destination = state["destination_icao"]
    reason = state.get("reason_unusable", "Unacceptable conditions")
    fuel = state.get("fuel_analysis", "")
    risk = state.get("risk_assessment", "")
    alternates = state.get("alternates", "")

    lines = [
        f"PRE-FLIGHT BRIEFING",
        f"{'='*50}",
        f"Route:    {departure} → {destination}",
        f"Verdict:  NO-GO",
        f"{'='*50}",
        "",
        f"REASON: {reason}",
        "",
    ]

    if risk:
        lines += ["RISK ASSESSMENT:", risk, ""]

    if fuel and "INSUFFICIENT" in fuel:
        lines += ["FUEL ANALYSIS:", fuel, ""]

    if alternates:
        lines += ["ALTERNATES CHECKED:", alternates, ""]
    else:
        lines.append("No viable alternates found within search radius.")

    lines += [
        "",
        "RECOMMENDATION: Do not depart.",
        "Monitor conditions and re-brief when situation improves.",
    ]

    briefing = "\n".join(lines)
    print(f"  [NO-GO Briefing] Generated")

    return {
        "go_no_go": "NO-GO",
        "briefing": briefing,
    }