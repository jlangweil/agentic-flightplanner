import json
import asyncio
from datetime import datetime, timezone, timedelta
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from app.state import BriefingState
from app.tools import all_tools
from app.config import settings

REACT_ANALYZER_SYSTEM = """You are an expert aviation weather analyst and 
dispatcher conducting a pre-flight safety assessment.

You have access to these tools:
- get_metar: current weather observation at an airport
- get_taf: forecast weather at an airport
- get_notams: active NOTAMs at an airport
- calculate_fuel: fuel requirements for the flight
- score_flight_risk: risk assessment based on weather and NOTAMs
- suggest_alternates: find alternate airports if destination is unusable
- check_night_currency: check if night landing requires currency

Your assessment process:
1. Check METAR and TAF at the DEPARTURE airport
2. Check METAR and TAF at the DESTINATION airport
3. Check NOTAMs at BOTH airports
4. Score the flight risk at the destination
5. Calculate fuel requirements if aircraft parameters are provided
6. If the query mentions multiple stops, check ALL intermediate airports
7. If destination risk is HIGH or EXTREME, find alternates automatically
8. If departure timing is provided, check night currency requirements

Be thorough. Always check both ends of the route.
Think step by step about what information you need before acting.
When done, provide a structured summary of your findings."""


def analyzer_react_node(state: BriefingState) -> dict:
    """
    ReAct-based analyzer that dynamically decides which tools to call
    and in what order. Handles multi-stop routes, ambiguous airports,
    and adaptive data gathering based on what it discovers.
    """
    departure   = state["departure_icao"]
    destination = state["destination_icao"]

    if not departure or not destination:
        return {
            "destination_is_unusable": True,
            "reason_unusable": "Could not resolve airport ICAO codes from query",
        }

    llm = ChatAnthropic(
        model=settings.llm_model,
        api_key=settings.anthropic_api_key,
    )

    react_agent = create_react_agent(llm, all_tools)

    # Build the assessment query with all available context
    query_lines = [
        f"Conduct a complete pre-flight assessment for:",
        f"Departure: {departure}",
        f"Destination: {destination}",
    ]

    if state.get("fuel_onboard_gal"):
        query_lines.append(
            f"Aircraft: {state['fuel_onboard_gal']} gal onboard, "
            f"{state['fuel_burn_gph']} GPH burn rate, "
            f"{state['true_airspeed_kts']} kts cruise speed"
        )

    if state.get("is_ifr"):
        query_lines.append("Flight rules: IFR")
    else:
        query_lines.append("Flight rules: VFR")

    if state.get("is_night_current") is False:
        query_lines.append("Pilot is NOT night current per FAR 61.57(b)")

    if state.get("carrying_passengers"):
        query_lines.append("Flight is carrying passengers")

    if state.get("departure_offset_minutes") is not None:
        offset = state["departure_offset_minutes"]
        now = datetime.now(timezone.utc)
        dep_time = now + timedelta(minutes=offset)
        query_lines.append(
            f"Planned departure: {dep_time.strftime('%H:%MZ')} UTC"
        )

    query = "\n".join(query_lines)

    print(f"  [ReAct Analyzer] Starting reasoning loop...")
    print(f"  [ReAct Analyzer] Query:\n{query}")

    messages = [
        SystemMessage(content=REACT_ANALYZER_SYSTEM),
        HumanMessage(content=query),
    ]

    result = react_agent.invoke({"messages": messages})

    # Log the reasoning trace
    tool_calls_made = []
    for msg in result["messages"]:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls_made.append(tc["name"])
                print(f"  [ReAct] → {tc['name']}({list(tc['args'].keys())})")
        elif hasattr(msg, "name") and msg.name:
            print(f"  [ReAct] ← {msg.name} returned")

    print(f"  [ReAct Analyzer] Tools called: {tool_calls_made}")

    final_response = result["messages"][-1].content
    print(f"  [ReAct Analyzer] Summary:\n{final_response}")

    # Extract tool results from message history
    return _extract_state(state, result["messages"], final_response)


def _extract_state(
    state: BriefingState,
    messages: list,
    summary: str,
) -> dict:
    """
    Walk the ReAct message history and map tool outputs
    back to BriefingState fields.
    """
    updates = {
        "departure_metar":         None,
        "departure_taf":           None,
        "departure_notams":        None,
        "destination_metar":       None,
        "destination_taf":         None,
        "destination_notams":      None,
        "risk_assessment":         None,
        "fuel_analysis":           None,
        "alternates":              None,
        "night_currency_check":    None,
        "destination_is_unusable": False,
        "reason_unusable":         None,
    }

    departure   = state["departure_icao"]
    destination = state["destination_icao"]

    for msg in messages:
        if not hasattr(msg, "name") or not msg.name:
            continue

        content = msg.content or ""

        if msg.name == "get_metar":
            # Determine which airport this belongs to
            if departure in content:
                updates["departure_metar"] = content
            elif destination in content:
                updates["destination_metar"] = content

        elif msg.name == "get_taf":
            if departure in content:
                updates["departure_taf"] = content
            elif destination in content:
                updates["destination_taf"] = content

        elif msg.name == "get_notams":
            if departure in content:
                updates["departure_notams"] = content
            elif destination in content:
                updates["destination_notams"] = content

        elif msg.name == "score_flight_risk":
            updates["risk_assessment"] = content
            if "NO-GO" in content or "EXTREME" in content:
                updates["destination_is_unusable"] = True
                updates["reason_unusable"] = _extract_reason(content)

        elif msg.name == "calculate_fuel":
            updates["fuel_analysis"] = content
            if "INSUFFICIENT" in content:
                updates["destination_is_unusable"] = True
                updates["reason_unusable"] = "Insufficient fuel"

        elif msg.name == "suggest_alternates":
            updates["alternates"] = content

        elif msg.name == "check_night_currency":
            updates["night_currency_check"] = content

    return updates


def _extract_reason(risk_text: str) -> str:
    for line in risk_text.splitlines():
        if "CRITICAL" in line or "WARNING" in line:
            return line.strip().lstrip("- ")
    return "High risk conditions at destination"