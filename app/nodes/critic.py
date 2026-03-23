from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from app.state import BriefingState
from app.config import settings

CRITIC_SYSTEM = """You are a senior certificated flight instructor (CFII) and
aviation safety expert conducting a pre-flight briefing review.

You will be given a flight plan summary including:
- Departure and destination airports
- Current weather (METAR) at both airports
- TAF forecast at both airports
- NOTAMs at both airports
- En-route weather at corridor airports (METAR/TAF or GFS MOS depending on horizon)
- Risk assessment score and factors
- Fuel analysis
- Any alternate airports found

Your job is to critically review this information and identify anything
that was missed, understated, or that warrants extra caution.

You must respond in this exact format:

VERDICT: [AGREE | DISAGREE | CAUTION]
SUMMARY: [One sentence summary of your assessment]
CONCERNS:
- [Concern 1]
- [Concern 2]
(list any specific concerns, or write "None" if you fully agree)

Rules:
- DISAGREE only if you find a genuine safety issue that warrants NO-GO
- CAUTION if conditions are acceptable but deserve specific attention
- AGREE if the assessment is thorough and the recommendation is sound
- Be specific — cite actual values from the weather data
- Apply FAR Part 91 standards
- Think like a CFII who cares about keeping pilots alive, not just legal
- Pay special attention to: deteriorating TAF trends, marginal fuel margins,
  NOTAMs affecting the planned approach, night currency, and icing potential
- If the pilot mentions not being night current, explicitly verify whether
  the ETA at destination is before or after end of civil twilight.
  If the night currency check shows a NO-GO, you must DISAGREE.
- A flight that departs VFR and arrives after civil twilight end with a
  non-night-current pilot is a hard NO-GO regardless of weather, unless they have a CFI onboard.

FORECAST HORIZON RULES — apply these based on what the en-route weather section reports:

  METAR_TAF horizon (0-30h out):
  - Current conditions are meaningful. Treat METAR/TAF data at full face value.
  - If any en-route airport shows IFR or LIFR, flag it explicitly as a concern.
  - MVFR en-route on a VFR flight warrants at least CAUTION.

  GFS_MOS horizon (30-72h out):
  - Only statistical model guidance (GFS MOS) is available — not certified TAFs.
  - MOS accuracy degrades beyond 48h. Treat forecasts as probabilistic, not definitive.
  - If MOS shows IFR or LIFR anywhere en-route, issue a CAUTION (not necessarily DISAGREE).
  - Always remind the pilot: re-brief with METAR/TAF within 24h of departure.
  - Do NOT issue AGREE without noting the reduced forecast reliability.

  NO_FORECAST horizon (>72h out):
  - No aviation weather product covers this departure window.
  - You MUST list this as a CONCERN: "Departure is beyond reliable forecast range
    (>72h). No METAR, TAF, or MOS product covers this window. A go/no-go decision
    cannot be made at this time — re-brief within 72h of planned departure."
  - Set VERDICT: CAUTION at minimum. If the pilot is treating this as a final
    go/no-go decision, set VERDICT: DISAGREE and explain why pre-departure planning
    at this range is premature.
"""


def _build_critic_prompt(state: BriefingState) -> str:
    """Build the full context block for the Critic to review."""
    departure = state["departure_icao"]
    destination = state["destination_icao"]
    is_ifr = state.get("is_ifr") or False
    is_night = state.get("is_night") or False

    offset = state.get("departure_offset_minutes")
    if offset is None:
        horizon_note = "Departure timing: not specified (treat as immediate)"
    elif offset > 4320:
        horizon_note = f"Departure timing: {offset/60:.0f}h from now — NO_FORECAST horizon (>72h)"
    elif offset > 1800:
        horizon_note = f"Departure timing: {offset/60:.0f}h from now — GFS_MOS horizon (30-72h)"
    else:
        horizon_note = f"Departure timing: {offset/60:.0f}h from now — METAR_TAF horizon (<30h)"

    sections = [
        f"FLIGHT: {departure} -> {destination}",
        f"IFR: {is_ifr}  Night: {is_night}",
        f"Forecast horizon: {horizon_note}",
        "",
    ]

    if state.get("departure_metar"):
        sections += ["DEPARTURE METAR:", state["departure_metar"], ""]

    if state.get("departure_taf"):
        sections += ["DEPARTURE TAF:", state["departure_taf"], ""]

    if state.get("departure_notams"):
        sections += ["DEPARTURE NOTAMs:", state["departure_notams"], ""]

    if state.get("destination_metar"):
        sections += ["DESTINATION METAR:", state["destination_metar"], ""]

    if state.get("destination_taf"):
        sections += ["DESTINATION TAF:", state["destination_taf"], ""]

    if state.get("destination_notams"):
        sections += ["DESTINATION NOTAMs:", state["destination_notams"], ""]

    if state.get("route_weather"):
        sections += ["EN-ROUTE WEATHER:", state["route_weather"], ""]

    if state.get("alternates"):
        sections += ["ALTERNATES:", state["alternates"], ""]

    if state.get("risk_assessment"):
        sections += ["RISK ASSESSMENT:", state["risk_assessment"], ""]

    if state.get("fuel_analysis"):
        sections += ["FUEL ANALYSIS:", state["fuel_analysis"], ""]

    return "\n".join(sections)


def critic_node(state: BriefingState) -> dict:
    """
    Review the flight assessment and challenge the GO recommendation.
    A second LLM call with a CFII persona that looks for what was missed.
    """
    llm = ChatAnthropic(
        model=settings.llm_model,
        api_key=settings.anthropic_api_key,
    )

    prompt = _build_critic_prompt(state)

    print("  [Critic] Reviewing assessment...")

    messages = [
        SystemMessage(content=CRITIC_SYSTEM),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)
    feedback = response.content.strip()

    print(f"  [Critic] Feedback:\n{feedback}")

    # Parse the verdict
    go_no_go = state.get("go_no_go")
    if "VERDICT: DISAGREE" in feedback:
        go_no_go = "NO-GO"
        print("  [Critic] Overriding to NO-GO")
    elif "VERDICT: CAUTION" in feedback:
        go_no_go = "MARGINAL"
        print("  [Critic] Flagging as MARGINAL")
    elif "VERDICT: AGREE" in feedback:
        # Only set GO if not already set to something worse
        if not go_no_go:
            go_no_go = "GO"
        print("  [Critic] Agrees with assessment")

    return {
        "critic_feedback": feedback,
        "go_no_go": go_no_go,
    }