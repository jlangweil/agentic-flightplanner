from app.startup import initialize
from app.runner import run_briefing

def test_agent():
    initialize()
    query = (
        "Should I fly from Morristown to Block Island today? "
        "My Cessna burns 10 GPH, cruises at 120 knots, "
        "and I have 40 gallons on board."
    )
    briefing = run_briefing(query)
    print(f"\nFinal briefing length: {len(briefing)} chars")

if __name__ == "__main__":
    test_agent()