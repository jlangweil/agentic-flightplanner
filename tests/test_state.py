# tests/test_state.py
from app.state import BriefingState, initial_state

def test_state():
    state = initial_state("Should I fly from KMMU to KBID today?")

    assert state["query"] == "Should I fly from KMMU to KBID today?"
    assert state["departure_icao"] == ""
    assert state["destination_icao"] == ""
    assert state["destination_is_unusable"] is False
    assert state["human_approved"] is False
    assert state["messages"] == []
    assert state["briefing"] is None
    assert state["go_no_go"] is None

    print("All state fields:")
    for k, v in state.items():
        print(f"  {k:30s} = {repr(v)}")
    print("\nState OK")

if __name__ == "__main__":
    test_state()