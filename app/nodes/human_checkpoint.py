from langgraph.types import interrupt
from app.state import BriefingState


def human_checkpoint_node(state: BriefingState) -> dict:
    """
    Pause the graph and present the assessment to the pilot for review.
    The graph will not continue until the pilot explicitly confirms.
    Uses LangGraph interrupt() — state is persisted to the checkpointer.
    """
    departure = state["departure_icao"]
    destination = state["destination_icao"]
    go_no_go = state.get("go_no_go", "UNKNOWN")
    risk = state.get("risk_assessment", "No risk assessment available")
    fuel = state.get("fuel_analysis", "No fuel analysis available")
    critic = state.get("critic_feedback", "No critic feedback available")
    alternates = state.get("alternates", "")

    # Build the summary presented to the pilot
    summary_lines = [
        f"AGENT ASSESSMENT SUMMARY",
        f"{'='*50}",
        f"Route:    {departure} → {destination}",
        f"Verdict:  {go_no_go}",
        f"{'='*50}",
        "",
        "RISK ASSESSMENT:",
        risk,
        "",
        "FUEL ANALYSIS:",
        fuel,
    ]

    if alternates:
        summary_lines += ["", "ALTERNATES:", alternates]

    summary_lines += [
        "",
        "CRITIC REVIEW:",
        critic,
        "",
        f"{'='*50}",
        "Do you want to proceed with this flight?",
        "Reply GO to generate full briefing, or NO-GO to abort.",
    ]

    summary = "\n".join(summary_lines)

    # interrupt() pauses the graph here and returns summary to the caller.
    # The value passed to interrupt() is what the caller receives while waiting.
    # Execution resumes when .invoke() is called again with the human's response.
    pilot_response = interrupt(summary)

    # When we resume, pilot_response contains what the human sent back
    confirmed = str(pilot_response).strip().upper() in ("GO", "YES", "CONFIRM", "Y")

    print(f"  [Checkpoint] Pilot response: {pilot_response}")
    print(f"  [Checkpoint] Confirmed: {confirmed}")

    return {
        "human_approved": confirmed,
        "go_no_go": "GO" if confirmed else "NO-GO",
    }