import json
from langchain.tools import tool
from pydantic import BaseModel, Field


class RiskInput(BaseModel):
    metar_json: str = Field(
        description="JSON string of MetarData for the destination airport"
    )
    taf_json: str = Field(
        description=(
            "JSON string of TafData for the destination airport. "
            "Pass empty string if no TAF is available."
        )
    )
    notams_text: str = Field(
        description=(
            "Text block of relevant NOTAMs as returned by get_notams tool. "
            "Pass empty string if no NOTAMs."
        )
    )
    is_ifr_rated: bool = Field(
        default=True,
        description="Whether the pilot holds an instrument rating"
    )
    is_night: bool = Field(
        default=False,
        description="Whether the flight is at night"
    )


def _score_visibility(vis: float | None) -> tuple[int, list[str]]:
    factors = []
    score = 0
    if vis is None:
        return 0, []
    if vis < 0.25:
        score += 5
        factors.append("CRITICAL: Near zero visibility")
    elif vis < 1:
        score += 4
        factors.append("CRITICAL: Visibility below 1SM")
    elif vis < 3:
        score += 2
        factors.append("WARNING: Visibility below 3SM (VFR minimum)")
    elif vis < 5:
        score += 1
        factors.append("CAUTION: Visibility below 5SM (MVFR)")
    return score, factors


def _score_ceiling(ceiling_ft: int | None, coverage: str | None) -> tuple[int, list[str]]:
    factors = []
    score = 0
    if ceiling_ft is None or coverage not in ("BKN", "OVC"):
        return 0, []
    if ceiling_ft < 200:
        score += 5
        factors.append("CRITICAL: Ceiling below 200ft")
    elif ceiling_ft < 500:
        score += 4
        factors.append("CRITICAL: Ceiling below 500ft")
    elif ceiling_ft < 1000:
        score += 2
        factors.append("WARNING: Ceiling below 1000ft (IFR)")
    elif ceiling_ft < 3000:
        score += 1
        factors.append("CAUTION: Ceiling below 3000ft (MVFR)")
    return score, factors


def _score_wind(
    wind_dir: int | str | None,
    wind_speed: int | None,
    wind_gust: int | None,
) -> tuple[int, list[str]]:
    factors = []
    score = 0

    # Normalize speed
    if wind_speed is None:
        return 0, []
    try:
        wind_speed = int(wind_speed)
    except (ValueError, TypeError):
        return 0, []

    # Normalize gust
    if wind_gust is not None:
        try:
            wind_gust = int(wind_gust)
        except (ValueError, TypeError):
            wind_gust = None

    # Handle variable direction — still score on speed/gust
    is_variable = (
        wind_dir is None or
        str(wind_dir).upper() == "VRB" or
        "V" in str(wind_dir).upper()
    )
    if is_variable and wind_speed > 0:
        factors.append(f"CAUTION: Variable wind direction at {wind_speed}kts")
        score += 1

    if wind_gust and wind_gust > 30:
        score += 3
        factors.append(f"CRITICAL: Gusts to {wind_gust}kts")
    elif wind_gust and wind_gust > 20:
        score += 1
        factors.append(f"WARNING: Gusts to {wind_gust}kts")

    if wind_speed > 25:
        score += 2
        factors.append(f"WARNING: Sustained winds {wind_speed}kts")
    elif wind_speed > 15:
        score += 1
        factors.append(f"CAUTION: Sustained winds {wind_speed}kts")

    return score, factors


def _score_weather_string(wx: str | None) -> tuple[int, list[str]]:
    factors = []
    score = 0
    if not wx:
        return 0, []
    upper = wx.upper()
    if any(k in upper for k in ["TS", "TSRA", "TSGR"]):
        score += 5
        factors.append("CRITICAL: Thunderstorm reported")
    if any(k in upper for k in ["FG", "FZFG"]):
        score += 3
        factors.append("CRITICAL: Fog reported")
    if any(k in upper for k in ["FZRA", "FZDZ", "IC", "PL"]):
        score += 4
        factors.append("CRITICAL: Freezing precipitation or ice")
    if any(k in upper for k in ["SN", "BLSN"]):
        score += 2
        factors.append("WARNING: Snow reported")
    if "RA" in upper:
        score += 1
        factors.append("CAUTION: Rain reported")
    return score, factors


def _score_notams(notams_text: str) -> tuple[int, list[str]]:
    factors = []
    score = 0
    if not notams_text.strip():
        return 0, []
    upper = notams_text.upper()
    if any(k in upper for k in ["RWY", "RUNWAY"]) and \
       any(k in upper for k in ["CLSD", "CLOSED"]):
        score += 4
        factors.append("CRITICAL: Runway closure in effect")
    if any(k in upper for k in ["ILS", "LOC", "GLIDE", "GS"]) and \
       any(k in upper for k in ["UNSERVICEABLE", "U/S", "UNMON", "OUT OF SERVICE"]):
        score += 3
        factors.append("WARNING: ILS or approach navaid unserviceable")
    if "PAPI" in upper or "VASI" in upper:
        if any(k in upper for k in ["UNSERVICEABLE", "U/S", "OUT OF SERVICE"]):
            score += 1
            factors.append("CAUTION: Visual approach slope indicator unserviceable")
    if "TFR" in upper:
        score += 2
        factors.append("WARNING: TFR active in area")
    return score, factors


def _score_forecast(taf_data: dict) -> tuple[int, list[str]]:
    """Check if the TAF shows deteriorating conditions."""
    factors = []
    score = 0
    periods = taf_data.get("forecast_periods", [])
    for period in periods:
        change = period.get("change_type", "")
        vis = period.get("visibility_sm")
        ceil = period.get("ceiling_ft")
        coverage = period.get("ceiling_coverage")

        # Only flag TEMPO and BECMG changes that bring bad conditions
        if change in ("TEMPO", "BECMG", "FM"):
            if vis is not None and vis < 3:
                score += 1
                factors.append(
                    f"WARNING: TAF shows visibility dropping to {vis}SM "
                    f"({change})"
                )
            if ceil is not None and coverage in ("BKN", "OVC") and ceil < 1000:
                score += 1
                factors.append(
                    f"WARNING: TAF shows ceiling dropping to {ceil}ft "
                    f"({change})"
                )
    return score, factors


@tool("score_flight_risk", args_schema=RiskInput)
def score_risk_tool(
    metar_json: str,
    taf_json: str,
    notams_text: str,
    is_ifr_rated: bool = True,
    is_night: bool = False,
) -> str:
    """
    Score the overall risk of a flight based on current weather, forecast,
    and NOTAMs. Returns a risk score 0-10+, risk level, and list of specific
    risk factors found. Always call this before issuing a GO/NO-GO verdict.
    A score of 6 or above should result in a NO-GO recommendation.
    """
    all_factors = []
    total_score = 0

    # Parse METAR
    try:
        metar = json.loads(metar_json)
    except (json.JSONDecodeError, TypeError):
        return "ERROR: Could not parse METAR data"

    # Parse TAF (optional)
    taf = {}
    if taf_json and taf_json.strip():
        try:
            taf = json.loads(taf_json)
        except (json.JSONDecodeError, TypeError):
            pass

    # Score each factor
    s, f = _score_visibility(metar.get("visibility_sm"))
    total_score += s
    all_factors.extend(f)

    s, f = _score_ceiling(metar.get("ceiling_ft"), metar.get("ceiling_coverage"))
    total_score += s
    all_factors.extend(f)

    s, f = _score_wind(metar.get("wind_dir"), metar.get("wind_speed_kts"), metar.get("wind_gust_kts"))
    total_score += s
    all_factors.extend(f)

    s, f = _score_weather_string(metar.get("weather"))
    total_score += s
    all_factors.extend(f)

    s, f = _score_notams(notams_text)
    total_score += s
    all_factors.extend(f)

    s, f = _score_forecast(taf)
    total_score += s
    all_factors.extend(f)

    # Night flying penalty
    if is_night:
        total_score += 1
        all_factors.append("CAUTION: Night flight — reduced visual references")

    # Risk level
    if total_score >= 9:
        risk_level = "EXTREME"
        verdict = "NO-GO"
    elif total_score >= 6:
        risk_level = "HIGH"
        verdict = "NO-GO"
    elif total_score >= 3:
        risk_level = "MEDIUM"
        verdict = "CAUTION — review carefully"
    else:
        risk_level = "LOW"
        verdict = "GO"

    # Format output
    lines = [
        f"Risk Assessment",
        f"  Score:      {total_score}/10+",
        f"  Level:      {risk_level}",
        f"  Verdict:    {verdict}",
        f"  Flight cat: {metar.get('flight_category', 'UNKNOWN')}",
    ]

    if all_factors:
        lines.append("  Factors:")
        for factor in all_factors:
            lines.append(f"    - {factor}")
    else:
        lines.append("  Factors: None — conditions look good")

    return "\n".join(lines)