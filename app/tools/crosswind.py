import math
from langchain.tools import tool
from pydantic import BaseModel, Field


class CrosswindInput(BaseModel):
    icao: str = Field(description="ICAO code of the airport")
    wind_dir: str = Field(
        description="Wind direction in degrees true (e.g. '270'), or 'VRB' for variable"
    )
    wind_speed_kts: int = Field(description="Wind speed in knots")
    wind_gust_kts: int | None = Field(
        default=None, description="Gust speed in knots, if present in METAR"
    )
    max_crosswind_kts: float = Field(
        default=15.0,
        description=(
            "Aircraft demonstrated crosswind component limit in knots. "
            "Defaults to 15 kts — pilot should verify their aircraft's POH value."
        ),
    )


def _crosswind_component(wind_deg: int, heading: float, speed: int) -> float:
    """Return the crosswind component in knots (always positive)."""
    angle = abs(wind_deg - heading) % 360
    if angle > 180:
        angle = 360 - angle
    return abs(speed * math.sin(math.radians(angle)))


@tool("check_crosswind", args_schema=CrosswindInput)
def check_crosswind_tool(
    icao: str,
    wind_dir: str,
    wind_speed_kts: int,
    wind_gust_kts: int | None = None,
    max_crosswind_kts: float = 15.0,
) -> str:
    """
    Calculate the crosswind component for all runways at an airport and determine
    which runway gives the smallest crosswind, and whether it is within the
    aircraft's demonstrated crosswind limit.

    Uses runway heading data from the airport database. Call this whenever the
    destination METAR reports non-calm, non-variable winds.
    """
    from app.airport_db import get_runway_headings

    headings = get_runway_headings(icao)
    if not headings:
        return (
            f"Crosswind Check — {icao}\n"
            f"  No runway heading data available — verify crosswind manually"
        )

    # Variable wind
    is_variable = (
        str(wind_dir).upper() == "VRB"
        or ("V" in str(wind_dir).upper() and str(wind_dir).upper() != "VRB")
    )
    if is_variable:
        lines = [
            f"Crosswind Check — {icao}",
            f"  Wind: VRB at {wind_speed_kts}kts"
            + (f" G{wind_gust_kts}kts" if wind_gust_kts else ""),
            f"  Aircraft limit: {max_crosswind_kts}kts (default — verify POH)",
        ]
        if wind_speed_kts > max_crosswind_kts:
            lines.append(f"  WARNING: Variable wind speed exceeds aircraft crosswind limit")
        elif wind_speed_kts > max_crosswind_kts * 0.8:
            lines.append(
                f"  CAUTION: Variable wind {wind_speed_kts}kts is near aircraft limit — "
                f"crosswind may approach limit on any runway"
            )
        else:
            lines.append(
                f"  OK: Variable wind {wind_speed_kts}kts is well within aircraft limit — "
                f"direction variable but speed is low"
            )
        return "\n".join(lines)

    wind_deg = int(wind_dir)

    # Find best runway (minimum crosswind)
    best_cw = float("inf")
    best_rwy = None
    best_heading = None

    for rwy_id, heading in headings:
        cw = _crosswind_component(wind_deg, heading, wind_speed_kts)
        if cw < best_cw:
            best_cw = cw
            best_rwy = rwy_id
            best_heading = heading

    # Gust crosswind on best runway
    gust_cw = None
    if wind_gust_kts is not None and best_heading is not None:
        gust_cw = _crosswind_component(wind_deg, best_heading, wind_gust_kts)

    effective_cw = max(best_cw, gust_cw or 0.0)

    lines = [
        f"Crosswind Check — {icao}",
        f"  Wind: {wind_deg:03d}° at {wind_speed_kts}kts"
        + (f" G{wind_gust_kts}kts" if wind_gust_kts else ""),
        f"  Best runway: {best_rwy} — crosswind {best_cw:.1f}kts",
    ]
    if gust_cw is not None:
        lines.append(f"  Best runway gust crosswind: {gust_cw:.1f}kts")
    lines.append(
        f"  Aircraft crosswind limit: {max_crosswind_kts}kts (default — verify POH)"
    )

    if effective_cw > max_crosswind_kts:
        lines += [
            f"  WARNING: Crosswind {effective_cw:.1f}kts EXCEEDS aircraft limit of {max_crosswind_kts}kts",
            f"  No runway at {icao} is within demonstrated crosswind limit",
        ]
    elif effective_cw > max_crosswind_kts * 0.8:
        lines.append(
            f"  CAUTION: Crosswind {effective_cw:.1f}kts is near aircraft limit ({max_crosswind_kts}kts)"
        )
    else:
        lines.append(f"  OK: Crosswind {effective_cw:.1f}kts is within aircraft limit")

    return "\n".join(lines)
