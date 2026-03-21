import asyncio
from langchain.tools import tool
from pydantic import BaseModel, Field
from app.fetchers import get_metar, get_taf, fetch_notams


# ── Input schemas ──────────────────────────────────────────────────────────
# These tell the LLM exactly what parameters each tool expects.
# The field descriptions are read by the model — write them clearly.

class AirportInput(BaseModel):
    icao: str = Field(
        description="4-letter ICAO airport code, e.g. KMMU, KBID, KTEB"
    )


# ── Tools ──────────────────────────────────────────────────────────────────

@tool("get_metar", args_schema=AirportInput)
def get_metar_tool(icao: str) -> str:
    """
    Fetch the current METAR (weather observation) for an airport.
    Returns current visibility, ceiling, wind, temperature, and flight
    category (VFR/MVFR/IFR/LIFR).
    Use this for current conditions at departure and destination airports.
    """
    result = asyncio.run(get_metar(icao))
    if result is None:
        return f"No METAR data available for {icao}"
    return result.model_dump_json()


@tool("get_taf", args_schema=AirportInput)
def get_taf_tool(icao: str) -> str:
    """
    Fetch the TAF (terminal area forecast) for an airport.
    Returns forecast conditions for the next 24 hours including
    expected visibility, ceiling, wind, and any TEMPO or BECMG changes.
    Use this to evaluate whether conditions will deteriorate during the flight.
    Small airports may not have a TAF — that is normal.
    """
    result = asyncio.run(get_taf(icao))
    if result is None:
        return f"No TAF available for {icao} — this is normal for small airports"
    return result.model_dump_json()


@tool("get_notams", args_schema=AirportInput)
def get_notams_tool(icao: str) -> str:
    """
    Fetch active NOTAMs (notices to air missions) for an airport.
    Returns operationally relevant notices including runway closures,
    navaid outages, airspace restrictions, and lighting issues.
    Always check NOTAMs for both departure and destination airports.
    """
    results = asyncio.run(fetch_notams(icao))
    if not results:
        return f"No active NOTAMs for {icao}"
    return "\n---\n".join(
        f"[{n.category}] {n.notam_id}: {n.excerpt or n.raw_text[:120]}"
        for n in results
    )