"""
En-route weather tool.

Finds airports within a corridor of the great-circle route and fetches the
appropriate forecast product based on how far in the future the flight is:

  0 – 30 h   (0 – 1 800 min):  METAR + TAF  (current / short-range)
  30 – 72 h  (1 800 – 4 320 min):  GFS MOS   (medium-range)
  > 72 h     (> 4 320 min):   NO_FORECAST  — no aviation product covers this window
"""
import asyncio
import math
from pydantic import BaseModel, Field
from langchain.tools import tool

# ── Forecast horizon constants ───────────────────────────────────────────────

HORIZON_METAR_TAF   = "METAR_TAF"
HORIZON_GFS_MOS     = "GFS_MOS"
HORIZON_NO_FORECAST = "NO_FORECAST"

_HORIZON_METAR_TAF_MAX_MIN  = 1_800   # 30 h
_HORIZON_GFS_MOS_MAX_MIN    = 4_320   # 72 h

# Flight category rank for worst-case summary
_CAT_RANK = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


# ── Input schema ─────────────────────────────────────────────────────────────

class RouteWeatherInput(BaseModel):
    departure_icao: str = Field(description="ICAO code of the departure airport")
    destination_icao: str = Field(description="ICAO code of the destination airport")
    departure_offset_minutes: float = Field(
        default=0.0,
        description=(
            "Minutes from now until planned departure (0 = leaving now). "
            "Drives forecast product selection: "
            "0-1800 min -> METAR+TAF, 1800-4320 min -> GFS MOS, >4320 min -> no forecast."
        ),
    )
    corridor_nm: float = Field(
        default=25.0,
        description="Search radius in nautical miles either side of the great-circle route",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _classify_horizon(offset_minutes: float) -> str:
    if offset_minutes <= _HORIZON_METAR_TAF_MAX_MIN:
        return HORIZON_METAR_TAF
    elif offset_minutes <= _HORIZON_GFS_MOS_MAX_MIN:
        return HORIZON_GFS_MOS
    else:
        return HORIZON_NO_FORECAST


def _flight_category(vis_sm: float | None, cig_ft: int | None, cld: str | None) -> str:
    """Derive FAA flight category. Only BKN/OVC count as ceiling."""
    has_ceil = cld in ("BKN", "OVC") and cig_ft is not None
    ceil = cig_ft if has_ceil else 99_999
    vis  = vis_sm if vis_sm is not None else 99.0
    if ceil < 500 or vis < 1:
        return "LIFR"
    if ceil < 1_000 or vis < 3:
        return "IFR"
    if ceil < 3_000 or vis < 5:
        return "MVFR"
    return "VFR"


# ── Async fetchers ────────────────────────────────────────────────────────────

async def _metar_taf_summary(icao: str) -> str | None:
    """One-line METAR + notable TAF trend for an en-route waypoint."""
    from app.fetchers import get_metar, get_taf

    metar, taf = await asyncio.gather(get_metar(icao), get_taf(icao))
    if not metar:
        return None

    parts = [f"    METAR ({metar.flight_category})"]
    if metar.wind_speed_kts is not None:
        wdir = str(metar.wind_dir) if metar.wind_dir else "VRB"
        parts.append(f"Wind:{wdir}/{metar.wind_speed_kts}kt")
    if metar.ceiling_ft is not None:
        parts.append(f"Ceil:{metar.ceiling_ft}ft {metar.ceiling_coverage or ''}")
    if metar.visibility_sm is not None:
        parts.append(f"Vis:{metar.visibility_sm}sm")

    line = " | ".join(parts)

    # Append first TAF period that shows a ceiling or visibility concern
    if taf and taf.forecast_periods:
        for p in taf.forecast_periods:
            if (p.ceiling_ft and p.ceiling_ft < 3_000) or (
                p.visibility_sm is not None and p.visibility_sm < 5
            ):
                t_label = str(p.time_from)[:16] if p.time_from else "?"
                line += (
                    f"\n    TAF trend from {t_label}: "
                    f"ceil {p.ceiling_ft}ft {p.ceiling_coverage or ''}"
                    + (f", vis {p.visibility_sm}sm" if p.visibility_sm is not None else "")
                )
                break

    return line


async def _mos_summary(icao: str, offset_minutes: float) -> str | None:
    """One-line GFS MOS forecast for an en-route waypoint at the target time."""
    from datetime import datetime, timezone, timedelta
    from app.fetchers import get_mos, mos_cig_to_ft, mos_vis_to_sm

    mos = await get_mos(icao)
    if not mos or not mos.periods:
        return None

    now = datetime.now(timezone.utc)
    target_dt = now + timedelta(minutes=offset_minutes)

    # Pick the period closest to the target departure time
    best = None
    best_delta = float("inf")
    for p in mos.periods:
        try:
            # API returns strings like "2025-03-21 18:00" or "2025032118"
            raw = str(p.ftime).strip()
            if " " in raw:
                period_dt = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            else:
                period_dt = datetime.strptime(raw[:10], "%Y%m%d%H").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        delta = abs((period_dt - target_dt).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best = p

    if not best:
        best = mos.periods[0]

    cig_ft = mos_cig_to_ft(best.cig)
    vis_sm = mos_vis_to_sm(best.vis)
    cat    = _flight_category(vis_sm, cig_ft, best.cld)

    parts = [f"    MOS {best.ftime} ({cat})"]
    if best.wsp is not None:
        wdr = f"{best.wdr:03d}" if best.wdr else "calm"
        parts.append(f"Wind:{wdr}/{best.wsp}kt")
    if cig_ft is not None:
        parts.append(f"Ceil:{cig_ft}ft {best.cld or ''}")
    elif best.cld == "CLR":
        parts.append("Sky:CLR")
    if vis_sm is not None:
        parts.append(f"Vis:{vis_sm:.1f}sm")
    if best.tmp is not None:
        parts.append(f"Temp:{best.tmp}F")

    return " | ".join(parts)


# ── Main tool ─────────────────────────────────────────────────────────────────

@tool("get_route_weather", args_schema=RouteWeatherInput)
def get_route_weather_tool(
    departure_icao: str,
    destination_icao: str,
    departure_offset_minutes: float = 0.0,
    corridor_nm: float = 25.0,
) -> str:
    """
    Fetch en-route weather for airports within corridor_nm of the great-circle
    route between departure and destination. Selects the appropriate forecast
    product based on departure timing:
      0-30 h:   METAR + TAF (current and short-range)
      30-72 h:  GFS MOS     (medium-range model guidance)
      >72 h:    NO_FORECAST advisory — no aviation product covers this window.
    Always call this for flights longer than ~50 nm.
    """
    from app.airport_db import find_corridor_airports

    horizon = _classify_horizon(departure_offset_minutes)

    # Hard gate: too far in the future for any reliable aviation forecast
    if horizon == HORIZON_NO_FORECAST:
        hours = departure_offset_minutes / 60
        return (
            f"EN-ROUTE WEATHER -- {departure_icao}->{destination_icao}\n"
            f"  FORECAST HORIZON: NO_FORECAST\n"
            f"  Planned departure is {hours:.0f}h from now "
            f"(>{_HORIZON_GFS_MOS_MAX_MIN // 60}h limit).\n"
            f"  No aviation forecast product covers this window:\n"
            f"    - METAR/TAF valid range: 0-30h\n"
            f"    - GFS MOS valid range:   0-72h\n"
            f"  Re-check all en-route weather within 72h of planned departure."
        )

    waypoint_airports = find_corridor_airports(
        departure_icao,
        destination_icao,
        corridor_nm=corridor_nm,
    )

    if not waypoint_airports:
        return (
            f"EN-ROUTE WEATHER -- {departure_icao}->{destination_icao}\n"
            f"  No airports found within {corridor_nm:.0f}nm of route corridor.\n"
            f"  (Short/direct route — departure and destination weather are sufficient)"
        )

    horizon_label = (
        "METAR+TAF (current, 0-30h)"
        if horizon == HORIZON_METAR_TAF
        else f"GFS MOS (medium-range, {departure_offset_minutes/60:.0f}h out)"
    )

    print(f"  [RouteWx] Checking {len(waypoint_airports)} en-route airports "
          f"within {corridor_nm:.0f}nm ({horizon_label.split('(')[0].strip()})...")

    # Fetch weather for all waypoints concurrently
    async def _gather():
        tasks = []
        for ap in waypoint_airports:
            if horizon == HORIZON_METAR_TAF:
                tasks.append(_metar_taf_summary(ap["icao"]))
            else:
                tasks.append(_mos_summary(ap["icao"], departure_offset_minutes))
        return await asyncio.gather(*tasks)

    try:
        results = asyncio.run(_gather())
    except Exception as e:
        return f"EN-ROUTE WEATHER: fetch failed -- {e}"

    lines = [
        f"EN-ROUTE WEATHER -- {departure_icao}->{destination_icao}",
        f"  Forecast product: {horizon_label}",
        f"  Corridor: {corridor_nm:.0f}nm either side of route",
        f"  En-route airports checked: {len(waypoint_airports)}",
        "",
    ]

    worst_cat = "VFR"
    for ap, result in zip(waypoint_airports, results):
        lines.append(
            f"  {ap['icao']} ({ap['name'][:35]}) -- {ap['dist_nm']}nm from route"
        )
        if result:
            lines.append(result)
            # Track worst flight category seen
            for token in result.split("|"):
                token = token.strip()
                for cat in ("LIFR", "IFR", "MVFR", "VFR"):
                    if cat in token:
                        if _CAT_RANK.get(cat, 0) > _CAT_RANK.get(worst_cat, 0):
                            worst_cat = cat
                        break
        else:
            lines.append(f"    No data available for {ap['icao']}")
        lines.append("")

    lines.append(f"  Worst en-route category: {worst_cat}")

    if worst_cat == "LIFR":
        lines.append(
            "  WARNING: LIFR conditions along route — "
            "VFR flight not safe, IFR required"
        )
    elif worst_cat == "IFR":
        lines.append(
            "  WARNING: IFR conditions along route — "
            "instrument capability required for safe passage"
        )
    elif worst_cat == "MVFR":
        lines.append(
            "  CAUTION: MVFR along route — "
            "monitor closely and have a divert plan"
        )

    if horizon == HORIZON_GFS_MOS:
        lines.append(
            "  NOTE: MOS is statistical model guidance, not a certified TAF. "
            "Re-brief with METAR/TAF within 24h of departure."
        )

    return "\n".join(lines)
