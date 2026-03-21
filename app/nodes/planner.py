import json
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from app.state import BriefingState
from app.config import settings

PLANNER_SYSTEM = """You are an aviation flight planning assistant.
Your job is to extract structured information from a pilot's query.

Extract the following and return as JSON only, no other text:
{
    "departure_icao": "4-letter ICAO code or empty string if not found",
    "destination_icao": "4-letter ICAO code or empty string if not found",
    "fuel_onboard_gal": null or number,
    "fuel_burn_gph": null or number,
    "true_airspeed_kts": null or number,
    "is_ifr": null or boolean,
    "is_night": null or boolean
}

Rules:
- Convert airport names to ICAO codes. Morristown NJ = KMMU, Block Island = KBID.
- If the pilot says "IFR flight plan" or "on an IFR" set is_ifr to true.
- If the pilot mentions night, dusk, or after sunset set is_night to true.
- If a value is not mentioned, use null.
- Return valid JSON only. No explanation, no markdown.
"""


def planner_node(state: BriefingState) -> dict:
    """
    Extract structured flight parameters from the user query.
    Writes departure/destination ICAO codes and aircraft params to state.
    """
    llm = ChatAnthropic(
        model=settings.llm_model,
        api_key=settings.anthropic_api_key,
    )

    messages = [
        SystemMessage(content=PLANNER_SYSTEM),
        HumanMessage(content=state["query"]),
    ]

    response = llm.invoke(messages)
    raw = response.content.strip()

    # Strip markdown code fences if the LLM adds them anyway
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback — return empty extractions, let downstream nodes handle it
        extracted = {}

    print(f"  [Planner] Departure:    {extracted.get('departure_icao', '?')}")
    print(f"  [Planner] Destination:  {extracted.get('destination_icao', '?')}")
    print(f"  [Planner] Fuel onboard: {extracted.get('fuel_onboard_gal')} gal")
    print(f"  [Planner] Burn rate:    {extracted.get('fuel_burn_gph')} GPH")
    print(f"  [Planner] Airspeed:     {extracted.get('true_airspeed_kts')} kts")
    print(f"  [Planner] IFR:          {extracted.get('is_ifr')}")

    return {
        "departure_icao":    extracted.get("departure_icao", ""),
        "destination_icao":  extracted.get("destination_icao", ""),
        "fuel_onboard_gal":  extracted.get("fuel_onboard_gal"),
        "fuel_burn_gph":     extracted.get("fuel_burn_gph"),
        "true_airspeed_kts": extracted.get("true_airspeed_kts"),
        "is_ifr":            extracted.get("is_ifr"),
        "is_night":          extracted.get("is_night"),
    }