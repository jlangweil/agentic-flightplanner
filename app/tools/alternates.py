import asyncio
from langchain.tools import tool
from pydantic import BaseModel, Field
from app.fetchers import get_metar
from app.airport_db import find_alternates, get_airport
from app.tools.risk import (
    _score_visibility,
    _score_ceiling,
    _score_wind,
    _score_weather_string,
)


class AlternatesInput(BaseModel):
    destination_icao: str = Field(
        description=(
            "ICAO code of the destination airport that is unusable. "
            "The tool will find nearby alternates within 75nm."
        )
    )
    reason: str = Field(
        description=(
            "Why the destination is unusable. "
            "e.g. 'IFR conditions', 'runway closed', 'below minimums'"
        )
    )
    radius_nm: float = Field(
        default=75,
        description="Search radius in nautical miles. Default 75nm."
    )
    min_runway_ft: int = Field(
        default=3000,
        description="Minimum runway length in feet. Default 3000ft."
    )


async def _evaluate_alternates(
    icao: str,
    radius_nm: float,
    min_runway_ft: int,
) -> list[dict]:
    """Fetch weather for each candidate and score them."""
    candidates = find_alternates(
        icao,
        radius_nm=radius_nm,
        min_runway_ft=min_runway_ft,
    )

    if not candidates:
        return []

    results = []
    for alt_icao in candidates:
        airport = get_airport(alt_icao)
        metar = await get_metar(alt_icao)

        if metar is None:
            results.append({
                "icao": alt_icao,
                "name": airport["name"] if airport else alt_icao,
                "available": False,
                "risk_score": 99,
            })
            continue

        score = 0
        factors = []
        for fn, args in [
            (_score_visibility,     [metar.visibility_sm]),
            (_score_ceiling,        [metar.ceiling_ft, metar.ceiling_coverage]),
            (_score_wind,           [metar.wind_dir, metar.wind_speed_kts, metar.wind_gust_kts]),
            (_score_weather_string, [metar.weather]),
        ]:
            s, f = fn(*args)
            score += s
            factors.extend(f)

        results.append({
            "icao": alt_icao,
            "name": airport["name"] if airport else alt_icao,
            "available": True,
            "flight_category": metar.flight_category,
            "visibility_sm": metar.visibility_sm,
            "ceiling_ft": metar.ceiling_ft,
            "ceiling_coverage": metar.ceiling_coverage,
            "wind_speed_kts": metar.wind_speed_kts,
            "wind_gust_kts": metar.wind_gust_kts,
            "risk_score": score,
            "factors": factors,
            "raw": metar.raw,
        })

    results.sort(key=lambda x: x.get("risk_score", 99))
    return results


@tool("suggest_alternates", args_schema=AlternatesInput)
def suggest_alternates_tool(
    destination_icao: str,
    reason: str,
    radius_nm: float = 75,
    min_runway_ft: int = 3000,
) -> str:
    """
    Find and evaluate alternate airports near an unusable destination.
    Searches a real airport database within the specified radius,
    filters by runway length, fetches live weather for each candidate,
    and ranks them best conditions first.
    Call this when destination weather is below minimums, a runway is
    closed, or any condition makes the destination airport unusable.
    """
    alternates = asyncio.run(
        _evaluate_alternates(destination_icao, radius_nm, min_runway_ft)
    )

    if not alternates:
        return (
            f"No viable alternates found within {radius_nm}nm of "
            f"{destination_icao} with runways >= {min_runway_ft}ft."
        )

    lines = [
        f"Alternates within {radius_nm}nm of {destination_icao.upper()}",
        f"Reason: {reason}",
        f"Runway minimum: {min_runway_ft}ft",
        "",
    ]

    best = None
    for i, alt in enumerate(alternates):
        rank = i + 1
        name = alt.get("name", alt["icao"])

        if not alt["available"]:
            lines.append(f"  {rank}. {alt['icao']} ({name}) — No weather data")
            continue

        cat = alt.get("flight_category", "UNKNOWN")
        vis = alt.get("visibility_sm", "?")
        ceil = alt.get("ceiling_ft")
        wind = alt.get("wind_speed_kts", 0)
        gust = alt.get("wind_gust_kts")
        score = alt.get("risk_score", 0)
        factors = alt.get("factors", [])

        ceil_str = f"{ceil}ft {alt.get('ceiling_coverage','')}" if ceil else "clear"
        wind_str = f"{wind}kts" + (f" G{gust}" if gust else "")

        status = (
            "RECOMMENDED" if score == 0 else
            "ACCEPTABLE"  if score < 3 else
            "MARGINAL"    if score < 6 else
            "AVOID"
        )

        if best is None and score < 6:
            best = f"{alt['icao']} ({name})"

        lines.append(f"  {rank}. {alt['icao']} — {name}")
        lines.append(f"     [{cat}] {status}  vis={vis}SM  "
                     f"ceil={ceil_str}  wind={wind_str}  risk={score}")
        for factor in factors[:2]:
            lines.append(f"     ! {factor}")

    lines.append("")
    lines.append(
        f"  Best alternate: {best}"
        if best else
        "  WARNING: No suitable alternates — all conditions poor"
    )

    return "\n".join(lines)