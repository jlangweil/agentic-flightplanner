import asyncio
import httpx
from langchain.tools import tool
from pydantic import BaseModel, Field

AVWX_BASE = "https://aviationweather.gov/api/data"

# Hazard types we treat as high-priority
_HIGH_PRIORITY_HAZARDS = {"TS", "TURB", "ICE", "FZRA", "VA", "TC"}


class SigmetInput(BaseModel):
    departure_icao: str = Field(description="ICAO code of the departure airport")
    destination_icao: str = Field(description="ICAO code of the destination airport")


async def _fetch_airsigmet(icao: str) -> list[dict]:
    """Fetch active SIGMETs/AIRMETs near an airport. Single attempt."""
    params = {"icaoLocation": icao.upper(), "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"{AVWX_BASE}/airsigmet", params=params)
            response.raise_for_status()
            if not response.text.strip():
                return []
            return response.json() or []
    except Exception as e:
        print(f"  [SIGMET] _fetch_airsigmet({icao}) failed: {e}")
        return []


def _format_entry(raw: dict) -> str:
    """Format one SIGMET/AIRMET entry as a single summary line."""
    kind = raw.get("airsigmetType", "SIGMET")
    hazard = raw.get("hazard", "")
    severity = raw.get("severity", "")
    def _fmt_time(v) -> str:
        if not v:
            return ""
        if isinstance(v, (int, float)):
            from datetime import datetime, timezone
            return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")
        return str(v)[:16].replace("T", " ")

    valid_from = _fmt_time(raw.get("validTimeFrom"))
    valid_to   = _fmt_time(raw.get("validTimeTo"))
    text = raw.get("rawAirSigmet", raw.get("alphaChar", ""))[:120]

    parts = [f"  [{kind}]"]
    if hazard:
        parts.append(hazard)
    if severity:
        parts.append(f"/{severity}")
    if valid_from and valid_to:
        parts.append(f"valid {valid_from}–{valid_to}Z")
    if text:
        parts.append(f"— {text.strip()}")
    return " ".join(parts)


@tool("get_sigmets_airmets", args_schema=SigmetInput)
def get_sigmet_tool(departure_icao: str, destination_icao: str) -> str:
    """
    Fetch active SIGMETs and AIRMETs affecting the departure and destination
    airports. SIGMETs cover severe weather hazards (thunderstorms, severe icing,
    severe turbulence, volcanic ash). AIRMETs cover moderate hazards
    (IFR conditions, moderate icing, turbulence, mountain obscuration).
    Always call this to check for airspace hazards along the route.
    """
    try:
        dep_raw  = asyncio.run(_fetch_airsigmet(departure_icao))
        dest_raw = asyncio.run(_fetch_airsigmet(destination_icao))
    except Exception as e:
        return f"SIGMETs/AIRMETs: fetch failed — {e}"

    # Deduplicate by alphaChar (unique ID field)
    seen: set[str] = set()
    entries: list[dict] = []
    for raw in dep_raw + dest_raw:
        uid = raw.get("alphaChar") or raw.get("rawAirSigmet", "")[:40]
        if uid not in seen:
            seen.add(uid)
            entries.append(raw)

    if not entries:
        return (
            f"SIGMETs/AIRMETs — {departure_icao}->{destination_icao}: "
            f"No active advisories"
        )

    sigmets = [e for e in entries if e.get("airsigmetType") == "SIGMET"]
    airmets = [e for e in entries if e.get("airsigmetType") != "SIGMET"]

    lines = [
        f"SIGMETs/AIRMETs — {departure_icao}->{destination_icao}: "
        f"{len(sigmets)} SIGMET(s), {len(airmets)} AIRMET(s)"
    ]

    if sigmets:
        lines.append("  SIGMETs (severe — treat as hard constraints):")
        for e in sigmets:
            lines.append(_format_entry(e))

    if airmets:
        lines.append("  AIRMETs (moderate — exercise caution):")
        for e in airmets:
            lines.append(_format_entry(e))

    # Top-level flags
    has_ts = any("TS" in (e.get("hazard") or "") for e in sigmets)
    has_ice_sigmet = any("ICE" in (e.get("hazard") or "") for e in sigmets)
    has_ice_airmet = any("ICE" in (e.get("hazard") or "") for e in airmets)
    has_ifr_airmet = any("IFR" in (e.get("hazard") or "") for e in airmets)

    if has_ts:
        lines.append("  WARNING: Convective SIGMET active — thunderstorm avoidance required")
    if has_ice_sigmet:
        lines.append("  WARNING: SIGMET for severe icing in effect")
    if has_ice_airmet:
        lines.append("  CAUTION: AIRMET for icing in effect")
    if has_ifr_airmet:
        lines.append("  CAUTION: AIRMET Sierra (IFR/mountain obscuration) in effect")

    return "\n".join(lines)
