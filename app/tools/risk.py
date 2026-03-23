import json
from langchain.tools import tool
from pydantic import BaseModel, Field
from typing import Optional


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
        default=False,
        description=(
            "True if the pilot holds an instrument rating AND the flight is filed IFR. "
            "False for VFR-only flights. This is CRITICAL: a VFR flight into IFR "
            "conditions scores as NO-GO regardless of other factors."
        ),
    )
    is_night: bool = Field(
        default=False,
        description="Whether the flight is at night"
    )
    ifr_current: bool = Field(
        default=False,
        description=(
            "True if the IFR-rated pilot is current (6 approaches + holds in past 6 months). "
            "False if the pilot is not current. Only relevant when is_ifr_rated=True."
        ),
    )
    personal_min_ceiling_ft: Optional[int] = Field(
        default=None,
        description=(
            "Pilot's personal minimum ceiling in feet. If the forecast ceiling is below this "
            "value, a CAUTION factor is added. Pass null if no personal minimums set."
        ),
    )
    personal_min_vis_sm: Optional[float] = Field(
        default=None,
        description=(
            "Pilot's personal minimum visibility in statute miles. If visibility is below "
            "this value, a CAUTION factor is added. Pass null if no personal minimums set."
        ),
    )


def _score_visibility(vis: float | None, is_ifr_rated: bool, ifr_current: bool) -> tuple[int, list[str]]:
    factors = []
    score = 0
    if vis is None:
        return 0, []

    if is_ifr_rated and ifr_current:
        # IFR-current pilot: only flag conditions below CAT I minimums (0.5 SM) as critical
        if vis < 0.25:
            score += 3
            factors.append("CRITICAL: Visibility below CAT II minimums (0.25SM)")
        elif vis < 0.5:
            score += 2
            factors.append("WARNING: Visibility below CAT I minimums (0.5SM)")
        elif vis < 1:
            score += 1
            factors.append("CAUTION: Low visibility — confirm approach minimums")
        elif vis < 3:
            score += 1
            factors.append("CAUTION: Visibility below 3SM — instrument approach likely required")
    elif is_ifr_rated:
        # IFR-rated but NOT current — still can fly IFR legally if acting as PIC solo,
        # but more risk. Score moderately.
        if vis < 0.25:
            score += 4
            factors.append("CRITICAL: Near zero visibility — IFR currency required")
        elif vis < 1:
            score += 3
            factors.append("CRITICAL: Visibility below 1SM — instrument approach required; verify currency")
        elif vis < 3:
            score += 2
            factors.append("WARNING: Visibility below 3SM — IFR conditions; currency required")
    else:
        # VFR pilot
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


def _score_ceiling(ceiling_ft: int | None, coverage: str | None, is_ifr_rated: bool, ifr_current: bool) -> tuple[int, list[str]]:
    factors = []
    score = 0
    if ceiling_ft is None or coverage not in ("BKN", "OVC"):
        return 0, []

    if is_ifr_rated and ifr_current:
        # IFR-current pilot: only flag below CAT I minimums (200ft) as critical
        if ceiling_ft < 100:
            score += 3
            factors.append("CRITICAL: Ceiling below CAT II minimums (100ft)")
        elif ceiling_ft < 200:
            score += 2
            factors.append("WARNING: Ceiling below CAT I minimums (200ft)")
        elif ceiling_ft < 500:
            score += 1
            factors.append("CAUTION: Low ceiling — confirm instrument approach available")
        elif ceiling_ft < 1000:
            score += 1
            factors.append("CAUTION: Ceiling below 1000ft — IFR conditions")
    elif is_ifr_rated:
        # IFR-rated but not current
        if ceiling_ft < 200:
            score += 4
            factors.append("CRITICAL: Ceiling below 200ft — IFR currency required; not current")
        elif ceiling_ft < 500:
            score += 3
            factors.append("CRITICAL: Ceiling below 500ft — instrument approach required; verify currency")
        elif ceiling_ft < 1000:
            score += 2
            factors.append("WARNING: Ceiling below 1000ft — IFR conditions; currency required")
        elif ceiling_ft < 3000:
            score += 1
            factors.append("CAUTION: Ceiling below 3000ft (MVFR)")
    else:
        # VFR pilot
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


def _score_weather_string(wx: str | None, is_ifr_rated: bool, ifr_current: bool) -> tuple[int, list[str]]:
    factors = []
    score = 0
    if not wx:
        return 0, []
    upper = wx.upper()

    # Thunderstorms and icing are dangerous regardless of pilot rating
    if any(k in upper for k in ["TS", "TSRA", "TSGR"]):
        score += 5
        factors.append("CRITICAL: Thunderstorm reported — NO-GO for all flights")
    if any(k in upper for k in ["FZRA", "FZDZ", "IC", "PL"]):
        score += 4
        factors.append("CRITICAL: Freezing precipitation or ice — NO-GO unless certified/equipped")
    if any(k in upper for k in ["FG", "FZFG"]):
        if is_ifr_rated and ifr_current:
            score += 1
            factors.append("CAUTION: Fog reported — verify approach minimums")
        else:
            score += 3
            factors.append("CRITICAL: Fog reported")
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


def _score_forecast(taf_data: dict, is_ifr_rated: bool, ifr_current: bool) -> tuple[int, list[str]]:
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
            # Threshold depends on pilot capability
            vis_threshold = 0.5 if (is_ifr_rated and ifr_current) else 3.0
            ceil_threshold = 200 if (is_ifr_rated and ifr_current) else 1000

            if vis is not None and vis < vis_threshold:
                score += 1
                factors.append(
                    f"WARNING: TAF shows visibility dropping to {vis}SM "
                    f"({change})"
                )
            if ceil is not None and coverage in ("BKN", "OVC") and ceil < ceil_threshold:
                score += 1
                factors.append(
                    f"WARNING: TAF shows ceiling dropping to {ceil}ft "
                    f"({change})"
                )
    return score, factors


def _score_personal_minimums(
    vis: float | None,
    ceiling_ft: int | None,
    coverage: str | None,
    personal_min_ceiling_ft: int | None,
    personal_min_vis_sm: float | None,
) -> tuple[int, list[str]]:
    factors = []
    score = 0
    if personal_min_vis_sm is not None and vis is not None:
        if vis < personal_min_vis_sm:
            score += 2
            factors.append(
                f"WARNING: Visibility {vis}SM is below your personal minimum of {personal_min_vis_sm}SM"
            )
    if personal_min_ceiling_ft is not None and ceiling_ft is not None and coverage in ("BKN", "OVC"):
        if ceiling_ft < personal_min_ceiling_ft:
            score += 2
            factors.append(
                f"WARNING: Ceiling {ceiling_ft}ft is below your personal minimum of {personal_min_ceiling_ft}ft"
            )
    return score, factors


@tool("score_flight_risk", args_schema=RiskInput)
def score_risk_tool(
    metar_json: str,
    taf_json: str,
    notams_text: str,
    is_ifr_rated: bool = False,
    is_night: bool = False,
    ifr_current: bool = False,
    personal_min_ceiling_ft: Optional[int] = None,
    personal_min_vis_sm: Optional[float] = None,
) -> str:
    """
    Score the overall risk of a flight based on current weather, forecast,
    and NOTAMs. Returns a risk score 0-10+, risk level, and list of specific
    risk factors found. Always call this before issuing a GO/NO-GO verdict.
    A score of 6 or above should result in a NO-GO recommendation.

    For IFR-rated and current pilots, ceiling/visibility are scored against
    instrument minimums (not VFR minimums). Only thunderstorms, icing,
    and conditions below CAT I/II minimums trigger high scores for IFR pilots.
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
    s, f = _score_visibility(metar.get("visibility_sm"), is_ifr_rated, ifr_current)
    total_score += s
    all_factors.extend(f)

    s, f = _score_ceiling(metar.get("ceiling_ft"), metar.get("ceiling_coverage"), is_ifr_rated, ifr_current)
    total_score += s
    all_factors.extend(f)

    s, f = _score_wind(metar.get("wind_dir"), metar.get("wind_speed_kts"), metar.get("wind_gust_kts"))
    total_score += s
    all_factors.extend(f)

    s, f = _score_weather_string(metar.get("weather"), is_ifr_rated, ifr_current)
    total_score += s
    all_factors.extend(f)

    s, f = _score_notams(notams_text)
    total_score += s
    all_factors.extend(f)

    s, f = _score_forecast(taf, is_ifr_rated, ifr_current)
    total_score += s
    all_factors.extend(f)

    s, f = _score_personal_minimums(
        metar.get("visibility_sm"),
        metar.get("ceiling_ft"),
        metar.get("ceiling_coverage"),
        personal_min_ceiling_ft,
        personal_min_vis_sm,
    )
    total_score += s
    all_factors.extend(f)

    # Night flying penalty
    if is_night:
        total_score += 1
        all_factors.append("CAUTION: Night flight — reduced visual references")

    # IFR currency warning (rated but not current)
    flight_cat = metar.get("flight_category", "")
    if is_ifr_rated and not ifr_current and flight_cat in ("IFR", "LIFR"):
        total_score = max(total_score, 4)
        all_factors.append(
            "WARNING: IFR conditions but pilot IFR currency has lapsed. "
            "Cannot legally fly IFR as PIC with passengers (FAR 61.57). "
            "Consider rescheduling until currency is restored."
        )

    # VFR-specific override: IFR/LIFR conditions at a VFR destination are a hard 10/10.
    # is_ifr_rated=False means this is a VFR-only flight.
    if not is_ifr_rated:
        if flight_cat in ("IFR", "LIFR"):
            total_score = 10          # hard maximum — no ambiguity
            all_factors.append(
                f"CRITICAL: VFR flight — destination is {flight_cat}. "
                "Legally and practically impossible to complete VFR. "
                "Visibility and/or ceiling are below FAR 91 VFR minimums. HARD NO-GO."
            )
        elif flight_cat == "MVFR":
            total_score = max(total_score, 4)   # at minimum MEDIUM
            all_factors.append(
                "CAUTION: VFR flight — destination is MVFR. "
                "Marginal conditions; divert plan required."
            )

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
        f"  Flight cat: {flight_cat or 'UNKNOWN'}",
        f"  IFR rated:  {is_ifr_rated}",
        f"  IFR current:{ifr_current}",
    ]

    if all_factors:
        lines.append("  Factors:")
        for factor in all_factors:
            lines.append(f"    - {factor}")
    else:
        lines.append("  Factors: None — conditions look good")

    return "\n".join(lines)
