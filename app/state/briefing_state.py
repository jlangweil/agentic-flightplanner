from typing import TypedDict, Optional
from langgraph.graph.message import add_messages
from typing import Annotated
from langchain_core.messages import BaseMessage


class BriefingState(TypedDict):
    # ── Input ──────────────────────────────────────────────────────────────
    # The original user query, exactly as typed
    query: str

    # Resolved from the query by the Planner
    departure_icao: str
    destination_icao: str

    # Aircraft parameters — extracted from query or prompted
    fuel_onboard_gal: Optional[float]
    fuel_burn_gph: Optional[float]
    true_airspeed_kts: Optional[float]
    is_ifr: Optional[bool]
    is_night: Optional[bool]
    # Night currency
    is_night_current: Optional[bool]
    departure_offset_minutes: Optional[float]   # minutes from now
    night_currency_check: Optional[str]          # output of the tool

    # ── Discovered during execution ────────────────────────────────────────
    # Raw tool outputs stored as JSON strings for the LLM to read
    departure_metar: Optional[str]
    departure_taf: Optional[str]
    departure_notams: Optional[str]

    destination_metar: Optional[str]
    destination_taf: Optional[str]
    destination_notams: Optional[str]

    # Alternate airports found if destination is unusable
    alternates: Optional[str]

    # ── Analysis outputs ───────────────────────────────────────────────────
    fuel_analysis: Optional[str]
    risk_assessment: Optional[str]
    critic_feedback: Optional[str]

    # ── Control flow flags ─────────────────────────────────────────────────
    # Set by the Analyzer node — drives the conditional edge
    destination_is_unusable: bool
    reason_unusable: Optional[str]

    # Set by the human checkpoint
    human_approved: bool

    carrying_passengers: Optional[bool]

    # ── Final output ───────────────────────────────────────────────────────
    go_no_go: Optional[str]        # "GO" | "NO-GO" | "MARGINAL"
    briefing: Optional[str]        # The final formatted briefing

    # ── Message history ────────────────────────────────────────────────────
    # LangGraph uses this to track the full conversation with the LLM
    # The add_messages reducer appends rather than overwrites
    messages: Annotated[list[BaseMessage], add_messages]

def initial_state(query: str) -> BriefingState:
    """
    Create a fresh BriefingState for a new query.
    All optional fields start as None, control flags start as False.
    """
    return BriefingState(
        query=query,
        departure_icao="",
        destination_icao="",
        fuel_onboard_gal=None,
        fuel_burn_gph=None,
        true_airspeed_kts=None,
        is_ifr=None,
        is_night=None,
        departure_metar=None,
        departure_taf=None,
        departure_notams=None,
        destination_metar=None,
        destination_taf=None,
        destination_notams=None,
        alternates=None,
        fuel_analysis=None,
        risk_assessment=None,
        critic_feedback=None,
        destination_is_unusable=False,
        reason_unusable=None,
        human_approved=False,
        go_no_go=None,
        briefing=None,
        messages=[],
        is_night_current=None,
        departure_offset_minutes=None,
        night_currency_check=None,
        carrying_passengers=None,
    )