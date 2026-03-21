from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from app.state import BriefingState
from app.config import settings

BRIEFING_SYSTEM = """You are a professional aviation dispatcher generating 
a formal pre-flight briefing document.

Given all available flight data, generate a complete, well-structured 
pre-flight briefing. Use clear aviation language. Be concise but thorough.

Structure your briefing exactly as follows:

PRE-FLIGHT BRIEFING
===================
Route:     [DEPARTURE] → [DESTINATION]  
Date/Time: [from weather observation time]
Verdict:   [GO / NO-GO / MARGINAL]

WEATHER SUMMARY
---------------
Departure ([ICAO]): [summary]
Destination ([ICAO]): [summary]
Forecast: [TAF highlights]

RISK FACTORS
------------
[List any risk factors, or "None identified"]

FUEL ANALYSIS
-------------
[Fuel summary]

NOTAMS
------
[Relevant NOTAMs, or "No significant NOTAMs"]

ALTERNATES
----------
[Alternate airports if applicable, or "N/A"]

CRITIC NOTES
------------
[Key points from the critic review]

RECOMMENDATION
--------------
[Final GO/NO-GO with specific guidance]

Keep the briefing factual, professional, and actionable.
"""


def final_briefing_node(state: BriefingState) -> dict:
    """
    Generate the final formatted pre-flight briefing.
    Called after human confirmation on GO flights,
    or directly for NO-GO flights that went through the Critic.
    """
    llm = ChatAnthropic(
        model=settings.llm_model,
        api_key=settings.anthropic_api_key,
    )

    # Build context for the LLM
    context_parts = [
        f"Flight: {state['departure_icao']} → {state['destination_icao']}",
        f"Verdict: {state.get('go_no_go', 'UNKNOWN')}",
        f"Human approved: {state.get('human_approved', False)}",
        "",
    ]

    for label, key in [
        ("Departure METAR",     "departure_metar"),
        ("Departure TAF",       "departure_taf"),
        ("Departure NOTAMs",    "departure_notams"),
        ("Destination METAR",   "destination_metar"),
        ("Destination TAF",     "destination_taf"),
        ("Destination NOTAMs",  "destination_notams"),
        ("Alternates",          "alternates"),
        ("Risk Assessment",     "risk_assessment"),
        ("Fuel Analysis",       "fuel_analysis"),
        ("Critic Feedback",     "critic_feedback"),
    ]:
        value = state.get(key)
        if value:
            context_parts += [f"{label}:", value, ""]

    context = "\n".join(context_parts)

    print("  [FinalBriefing] Generating briefing...")

    messages = [
        SystemMessage(content=BRIEFING_SYSTEM),
        HumanMessage(content=context),
    ]

    response = llm.invoke(messages)
    briefing = response.content.strip() if response.content else None

    if not briefing:
        print("  [FinalBriefing] WARNING: LLM returned empty response")
        # Fallback — build a simple briefing from state directly
        briefing = _fallback_briefing(state)

    print("  [FinalBriefing] Done")
    return {"briefing": briefing}


def _fallback_briefing(state: BriefingState) -> str:
    """Simple formatted briefing if LLM call fails."""
    lines = [
        "PRE-FLIGHT BRIEFING",
        "=" * 50,
        f"Route:   {state['departure_icao']} → {state['destination_icao']}",
        f"Verdict: {state.get('go_no_go', 'UNKNOWN')}",
        "=" * 50,
        "",
        state.get("risk_assessment", ""),
        "",
        state.get("fuel_analysis", ""),
        "",
        state.get("critic_feedback", ""),
    ]
    return "\n".join(l for l in lines if l is not None)