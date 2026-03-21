import uuid
from langgraph.types import Command
from app.agent import dispatcher
from app.state import initial_state


def run_briefing(query: str) -> str:
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    print(f"\n{'='*60}")
    print(f"Smart Dispatcher — Thread: {thread_id[:8]}...")
    print(f"{'='*60}\n")

    # ── First pass — runs until interrupt_before=["human_checkpoint"] ──────
    state = initial_state(query)
    result = dispatcher.invoke(state, config=config)

    # ── In LangGraph 0.3.x detect interrupt via graph state ────────────────
    graph_state = dispatcher.get_state(config)
    next_nodes = graph_state.next

    print(f"\n  [Runner] Next nodes: {next_nodes}")

    if "human_checkpoint" not in next_nodes:
        # Graph completed without interruption — NO-GO path
        briefing = result.get("briefing") or _extract_briefing(graph_state)
        print("\n" + "="*60)
        print(briefing or "No briefing generated")
        print("="*60)
        return briefing or "No briefing generated"

    # ── Build and present the assessment summary ────────────────────────────
    state_values = graph_state.values
    assessment = _build_assessment(state_values)

    print("\n" + "="*60)
    print(assessment)
    print("="*60)

    pilot_input = input("\nYour decision (GO / NO-GO): ").strip().upper()

    print(f"\n  [Runner] Pilot responded: {pilot_input}")

    # ── Resume with Command in LangGraph 0.3.x ─────────────────────────────
    final_result = dispatcher.invoke(
        Command(resume=pilot_input),
        config=config,
    )

    # Get final state
    final_state = dispatcher.get_state(config)
    briefing = final_state.values.get("briefing")

    if not briefing:
        briefing = _fallback_briefing(final_state.values)

    print("\n" + "="*60)
    print(briefing)
    print("="*60)

    return briefing


def _build_assessment(state_values: dict) -> str:
    """Build the assessment summary shown to the pilot before confirmation."""
    departure = state_values.get("departure_icao", "?")
    destination = state_values.get("destination_icao", "?")
    go_no_go = state_values.get("go_no_go", "UNKNOWN")
    risk = state_values.get("risk_assessment", "No risk assessment")
    fuel = state_values.get("fuel_analysis", "No fuel analysis")
    critic = state_values.get("critic_feedback", "No critic feedback")
    alternates = state_values.get("alternates", "")

    lines = [
        "AGENT ASSESSMENT SUMMARY",
        "=" * 50,
        f"Route:   {departure} → {destination}",
        f"Verdict: {go_no_go}",
        "=" * 50,
        "",
        "RISK ASSESSMENT:",
        risk,
        "",
        "FUEL ANALYSIS:",
        fuel,
    ]

    if alternates:
        lines += ["", "ALTERNATES:", alternates]

    lines += [
        "",
        "CRITIC REVIEW:",
        critic,
        "",
        "=" * 50,
        "Do you want to proceed with this flight?",
        "Type GO to generate full briefing, or NO-GO to abort.",
    ]

    return "\n".join(lines)


def _extract_briefing(graph_state) -> str | None:
    """Try to get briefing from graph state values."""
    return graph_state.values.get("briefing")


def _fallback_briefing(state_values: dict) -> str:
    """Simple briefing if LLM call fails."""
    lines = [
        "PRE-FLIGHT BRIEFING"
        "=" * 50,
        f"Route:   {state_values.get('departure_icao')} → "
        f"{state_values.get('destination_icao')}",
        f"Verdict: {state_values.get('go_no_go', 'UNKNOWN')}",
        "=" * 50,
        "",
        state_values.get("risk_assessment", ""),
        "",
        state_values.get("fuel_analysis", ""),
        "",
        state_values.get("critic_feedback", ""),
    ]
    return "\n".join(l for l in lines if l is not None)