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
"""


def _build_critic_prompt(state: BriefingState) -> str:
    """Build the full context block for the Critic to review."""
    departure = state["departure_icao"]
    destination = state["destination_icao"]
    is_ifr = state.get("is_ifr") or False
    is_night = state.get("is_night") or False

    sections = [
        f"FLIGHT: {departure} → {destination}",
        f"IFR: {is_ifr}  Night: {is_night}",
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