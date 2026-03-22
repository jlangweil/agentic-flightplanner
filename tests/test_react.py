from app.startup import initialize
from app.state import initial_state
from app.nodes.planner import planner_node
from app.nodes.analyzer_react import analyzer_react_node

def test_react():
    initialize()

    state = initial_state(
        "Should I fly from Caldwell NJ to Albany then on to Burlington VT KBTV "
        "VFR only, 40 gallons, 10 GPH, 120 knots."
    )

    state.update(planner_node(state))
    print(f"Departure:   {state['departure_icao']}")
    print(f"Destination: {state['destination_icao']}")
    print()

    updates = analyzer_react_node(state)

    print("\nState updates from ReAct analyzer:")
    for k, v in updates.items():
        if v:
            print(f"  {k}: {str(v)[:80]}")

if __name__ == "__main__":
    test_react()