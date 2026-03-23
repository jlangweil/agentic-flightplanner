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
    "is_night": null or boolean,
    "is_night_current": null or boolean,
    "departure_offset_minutes": null or number,
    "carrying_passengers": null or boolean,
    "ifr_current": null or boolean,
    "personal_min_ceiling_ft": null or number,
    "personal_min_vis_sm": null or number
}

Rules:
- Convert airport names to ICAO codes. Morristown NJ = KMMU,
  Block Island = KBID, Caldwell NJ = KCDW, Teterboro = KTEB.
- For small airports you don't know the ICAO for, return the airport name or
  FAA local code (e.g. "Goodspeed" or "42B") — the system will resolve it.
- 3-letter FAA codes like "1B1", "42B" are valid — return them as-is.
- If the pilot says "IFR flight plan" or "on an IFR" set is_ifr to true.
- If the pilot mentions night, dusk, or after sunset set is_night to true.
- If the pilot says "not night current" or "no night currency" set 
  is_night_current to false. If they say they are night current set to true.
  If not mentioned set to null.
- departure_offset_minutes: extract how many minutes from now they want 
  to leave. "leaving now" = 0, "in 1 hour" = 60, "in 30 minutes" = 30,
  "this afternoon at 3pm" = calculate from current time if possible,
  otherwise null.
- If a value is not mentioned, use null.
- Return valid JSON only. No explanation, no markdown.
- If the pilot mentions passengers, carrying people, or flying with someone
  set carrying_passengers to true. If explicitly solo set to false.
  If not mentioned set to null.
- ifr_current: set to true if pilot says they are IFR current, have done
  recent approaches, or mentions being current on instruments. Set to false
  if they say they're not current or haven't flown IFR recently. Null if not mentioned.
- personal_min_ceiling_ft: extract pilot's personal minimums ceiling in feet if mentioned.
  e.g. "my personal mins are 500ft ceiling" → 500. Null if not mentioned.
- personal_min_vis_sm: extract pilot's personal minimums visibility in statute miles.
  e.g. "personal minimums 2 miles visibility" → 2.0. Null if not mentioned.
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

    from app.airport_db import normalize_icao, get_airport, find_airport_by_name

    def _resolve(code: str) -> str:
        """
        Resolve an LLM-returned value (ICAO code or airport name) to the best
        airport identifier we have in the database.

        Order of attempts:
        1. Normalize the code as-is (handles K-prefix injection for short FAA codes)
        2. If that gives a known airport, done.
        3. Otherwise treat 'code' as a partial name and search by name.
        4. From name hits, prefer: K-prefix ICAO > raw ident (even if short)
        """
        if not code:
            return code

        resolved = normalize_icao(code)
        if get_airport(resolved):
            return resolved

        # LLM returned a name (e.g. "goodspeed") or an unknown code — search by name
        hits = find_airport_by_name(code)
        if hits:
            # Prefer a K-prefix ICAO with METAR capability
            for h in hits:
                k = "K" + h["icao"].lstrip("K")
                if get_airport(k):
                    print(f"  [Planner] Name-resolved '{code}' → {k} ({h['name']})")
                    return k
            # Fall back to the raw ident (may be 3-char; analyzer will proxy weather)
            best = hits[0]
            print(f"  [Planner] Name-resolved '{code}' → {best['icao']} ({best['name']})")
            return best["icao"]

        # Last resort: ask the LLM directly for the FAA/ICAO identifier
        print(f"  [Planner] DB lookup failed for '{code}' — asking LLM for identifier")
        try:
            resp = llm.invoke([
                SystemMessage(content=(
                    "You are an aviation database. "
                    "Return ONLY the FAA or ICAO airport identifier code for the given airport "
                    "(e.g. 'N30', 'KMMU', '42B'). "
                    "No explanation, no punctuation, just the code. "
                    "If you are not confident, return an empty string."
                )),
                HumanMessage(content=f"Airport identifier for: {code}"),
            ])
            llm_code = resp.content.strip().strip('"').strip("'").upper()
            if llm_code:
                llm_resolved = normalize_icao(llm_code)
                if get_airport(llm_resolved):
                    print(f"  [Planner] LLM-resolved '{code}' → {llm_resolved}")
                    return llm_resolved
                # Even if not in our DB, trust the LLM code over the unresolved name
                if llm_code != code.upper():
                    print(f"  [Planner] LLM returned '{llm_code}' for '{code}' (not in local DB)")
                    return llm_code
        except Exception as e:
            print(f"  [Planner] LLM fallback failed for '{code}': {e}")

        return resolved

    dep_icao  = _resolve(extracted.get("departure_icao",  ""))
    dest_icao = _resolve(extracted.get("destination_icao", ""))

    return {
        "departure_icao":          dep_icao,
        "destination_icao":        dest_icao,
        "fuel_onboard_gal":        extracted.get("fuel_onboard_gal"),
        "fuel_burn_gph":           extracted.get("fuel_burn_gph"),
        "true_airspeed_kts":       extracted.get("true_airspeed_kts"),
        "is_ifr":                  extracted.get("is_ifr"),
        "is_night":                extracted.get("is_night"),
        "is_night_current":        extracted.get("is_night_current"),
        "departure_offset_minutes": extracted.get("departure_offset_minutes"),
        "carrying_passengers":     extracted.get("carrying_passengers"),
        "ifr_current":             extracted.get("ifr_current"),
        "personal_min_ceiling_ft": extracted.get("personal_min_ceiling_ft"),
        "personal_min_vis_sm":     extracted.get("personal_min_vis_sm"),
    }