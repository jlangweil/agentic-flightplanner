from app.startup import initialize
from app.state import initial_state
from app.nodes.planner import planner_node
from app.nodes.analyzer import analyzer_node
from app.nodes.routing import route_after_analyzer, route_after_alternates
from app.nodes.critic import critic_node

def test_nodes():
    initialize()

    query = (
        "Should I fly from Morristown to Block Island today? "
        "My Cessna burns 10 GPH, cruises at 120 knots, "
        "and I have 40 gallons on board."
    )

    print(f"\nQuery: {query}\n")

    # Start with fresh state
    state = initial_state(query)

    # Run planner
    print("=== Planner Node ===")
    planner_updates = planner_node(state)
    state.update(planner_updates)
    print(f"  Departure:   {state['departure_icao']}")
    print(f"  Destination: {state['destination_icao']}")
    print(f"  Fuel:        {state['fuel_onboard_gal']} gal @ "
          f"{state['fuel_burn_gph']} GPH")
    print(f"  Airspeed:    {state['true_airspeed_kts']} kts")

    # Run analyzer
    print("\n=== Analyzer Node ===")
    analyzer_updates = analyzer_node(state)
    state.update(analyzer_updates)

    print(f"\n  Destination unusable: {state['destination_is_unusable']}")
    if state['reason_unusable']:
        print(f"  Reason: {state['reason_unusable']}")
    if state['risk_assessment']:
        print(f"\n  Risk Assessment:\n{state['risk_assessment']}")
    if state['fuel_analysis']:
        print(f"\n  Fuel Analysis:\n{state['fuel_analysis']}")
    
def test_routing():
    print("\n=== Routing Tests ===")

    # Test 1 — destination unusable → find alternates
    state = {
        "destination_is_unusable": True,
        "reason_unusable": "IFR conditions",
        "fuel_analysis": "",
        "alternates": "",
    }
    result = route_after_analyzer(state)
    assert result == "find_alternates", f"Expected find_alternates, got {result}"
    print(f"  Test 1 passed: {result}")

    # Test 2 — fuel insufficient → no_go_briefing
    state = {
        "destination_is_unusable": False,
        "fuel_analysis": "FUEL INSUFFICIENT — NO-GO",
        "alternates": "",
    }
    result = route_after_analyzer(state)
    assert result == "no_go_briefing", f"Expected no_go_briefing, got {result}"
    print(f"  Test 2 passed: {result}")

    # Test 3 — all clear → critic
    state = {
        "destination_is_unusable": False,
        "fuel_analysis": "FUEL OK",
        "alternates": "",
    }
    result = route_after_analyzer(state)
    assert result == "critic", f"Expected critic, got {result}"
    print(f"  Test 3 passed: {result}")

    # Test 4 — no alternates found → no_go_briefing
    state = {"alternates": "No viable alternates found within 75nm"}
    result = route_after_alternates(state)
    assert result == "no_go_briefing", f"Expected no_go_briefing, got {result}"
    print(f"  Test 4 passed: {result}")

    # Test 5 — alternate found → critic
    state = {"alternates": "1. KUUU — Newport State Airport\n  Best alternate: KUUU"}
    result = route_after_alternates(state)
    assert result == "critic", f"Expected critic, got {result}"
    print(f"  Test 5 passed: {result}")

    print("\nAll routing tests passed")

def test_critic():
    initialize()

    query = (
        "Should I fly from Morristown to Block Island today? "
        "My Cessna burns 10 GPH, cruises at 120 knots, "
        "and I have 40 gallons on board."
    )

    state = initial_state(query)

    print("\n=== Running full pipeline to Critic ===")

    state.update(planner_node(state))
    print(f"  Resolved: {state['departure_icao']} → "
          f"{state['destination_icao']}")

    state.update(analyzer_node(state))
    print(f"  Unusable: {state['destination_is_unusable']}")

    # Only run Critic if destination is usable
    # (otherwise NO-GO Briefing node handles it)
    if not state["destination_is_unusable"]:
        print("\n=== Critic Node ===")
        state.update(critic_node(state))
        print(f"\n  GO/NO-GO: {state['go_no_go']}")
        print(f"\n  Critic feedback:\n{state['critic_feedback']}")
    else:
        print(f"\n  Skipping Critic — destination unusable: "
              f"{state['reason_unusable']}")

if __name__ == "__main__":
    #test_nodes()
    #test_routing()
    test_critic()