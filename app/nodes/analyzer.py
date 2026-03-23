import asyncio
import json
from app.state import BriefingState
from app.fetchers import get_metar, get_taf, fetch_notams, get_pireps
from app.tools.risk import score_risk_tool
from app.tools.fuel import calculate_fuel_tool
from app.tools.sunset import check_night_currency_tool
from app.tools.crosswind import check_crosswind_tool
from app.tools.winds_aloft import get_winds_aloft_tool
from datetime import datetime, timezone, timedelta


async def _fetch_airport_data(icao: str) -> tuple[str, str, str]:
    """Fetch METAR, TAF, and NOTAMs for one airport concurrently."""
    metar, taf, notams = await asyncio.gather(
        get_metar(icao),
        get_taf(icao),
        fetch_notams(icao),
    )

    metar_str = metar.model_dump_json() if metar else ""
    taf_str = taf.model_dump_json() if taf else ""
    notams_str = (
        "\n---\n".join(
            f"[{n.category}] {n.notam_id}: {n.excerpt or n.raw_text[:120]}"
            for n in notams
        ) if notams else ""
    )
    return metar_str, taf_str, notams_str


async def _fetch_pireps_combined(dep_icao: str, dest_icao: str) -> str:
    """Fetch PIREPs for both airports, deduplicate, and return formatted string."""
    dep_pireps, dest_pireps = await asyncio.gather(
        get_pireps(dep_icao, radius_nm=100),
        get_pireps(dest_icao, radius_nm=100),
    )

    seen = set()
    combined = []
    for p in dep_pireps + dest_pireps:
        if p.raw not in seen:
            seen.add(p.raw)
            combined.append(p)

    if not combined:
        return ""

    from app.tools.pireps import _rank_pirep, _format_pirep_line
    significant = [p for p in combined if _rank_pirep(p) >= 3]
    routine = [p for p in combined if _rank_pirep(p) < 3]

    lines = [f"PIREPs — {dep_icao}→{dest_icao} corridor ({len(combined)} total, last 3h)"]
    if significant:
        lines.append(f"  SIGNIFICANT ({len(significant)}):")
        for p in significant[:6]:
            lines.append(_format_pirep_line(p))
        if len(significant) > 6:
            lines.append(f"  ... and {len(significant) - 6} more")
    else:
        lines.append("  No significant turbulence or icing reports")
    if routine:
        lines.append(f"  Routine ({len(routine)}):")
        for p in routine[:3]:
            lines.append(_format_pirep_line(p))

    from app.tools.pireps import _TURB_RANK, _ICING_RANK
    if any(_TURB_RANK.get(p.turbulence_intensity or "", 0) >= 4 for p in combined):
        lines.append("  WARNING: Severe turbulence reported along route")
    if any(_ICING_RANK.get(p.icing_intensity or "", 0) >= 3 for p in combined):
        lines.append("  WARNING: Moderate or greater icing reported along route")

    return "\n".join(lines)


def analyzer_node(state: BriefingState) -> dict:
    """
    Fetch all weather and NOTAM data for departure and destination.
    Score destination risk and set destination_is_unusable flag.
    Also runs fuel calculation if aircraft parameters are available.
    """
    departure = state["departure_icao"]
    destination = state["destination_icao"]

    if not departure or not destination:
        return {
            "destination_is_unusable": True,
            "reason_unusable": "Could not resolve airport ICAO codes from query",
        }

    import traceback
    from concurrent.futures import ThreadPoolExecutor

    print(f"  [Analyzer] Fetching data for {departure} and {destination}")

    # ── Both airports in one parallel asyncio.run ────────────────────────────
    async def _fetch_both():
        return await asyncio.gather(
            _fetch_airport_data(departure),
            _fetch_airport_data(destination),
        )

    (dep_metar, dep_taf, dep_notams), (dest_metar, dest_taf, dest_notams) = (
        asyncio.run(_fetch_both())
    )

    # ── Five supplementary checks in parallel threads ────────────────────────
    # Each runs asyncio.run() or a sync tool in its own thread, so they
    # don't block each other. Crosswind needs dest_metar (already fetched).

    def _run_pireps():
        try:
            r = asyncio.run(_fetch_pireps_combined(departure, destination))
            print(f"  [Analyzer] PIREPs fetched")
            return r
        except Exception as e:
            print(f"  [Analyzer] PIREPs ERROR: {e}\n{traceback.format_exc()}")
            return f"PIREPs unavailable: {e}"

    def _run_sigmets():
        try:
            from app.tools.sigmet import get_sigmet_tool
            r = get_sigmet_tool.invoke({"departure_icao": departure, "destination_icao": destination})
            print(f"  [Analyzer] SIGMETs/AIRMETs fetched")
            return r
        except Exception as e:
            print(f"  [Analyzer] SIGMETs ERROR: {e}\n{traceback.format_exc()}")
            return f"SIGMETs/AIRMETs unavailable: {e}"

    def _run_route_weather():
        try:
            from app.tools.route_weather import get_route_weather_tool
            r = get_route_weather_tool.invoke({
                "departure_icao": departure,
                "destination_icao": destination,
                "departure_offset_minutes": state.get("departure_offset_minutes") or 0.0,
            })
            print(f"  [Analyzer] Route weather fetched")
            return r
        except Exception as e:
            print(f"  [Analyzer] Route weather ERROR: {e}\n{traceback.format_exc()}")
            return f"En-route weather unavailable: {e}"

    def _run_crosswind():
        if not dest_metar:
            return ""
        try:
            dest_wind = json.loads(dest_metar)
            wind_dir  = dest_wind.get("wind_dir")
            wind_speed = dest_wind.get("wind_speed_kts")
            if wind_speed and wind_speed > 0 and wind_dir is not None:
                r = check_crosswind_tool.invoke({
                    "icao": destination,
                    "wind_dir": str(wind_dir),
                    "wind_speed_kts": int(wind_speed),
                    "wind_gust_kts": dest_wind.get("wind_gust_kts"),
                })
                print(f"  [Analyzer] Crosswind:\n{r}")
                return r
            return ""
        except Exception as e:
            print(f"  [Analyzer] Crosswind ERROR: {e}\n{traceback.format_exc()}")
            return f"Crosswind check unavailable: {e}"

    def _run_winds_aloft():
        if not state.get("true_airspeed_kts"):
            return ""
        try:
            r = get_winds_aloft_tool.invoke({
                "departure_icao": departure,
                "destination_icao": destination,
                "cruise_altitude_ft": 6000,
                "true_airspeed_kts": float(state["true_airspeed_kts"]),
            })
            print(f"  [Analyzer] Winds aloft:\n{r}")
            return r
        except Exception as e:
            print(f"  [Analyzer] Winds aloft ERROR: {e}\n{traceback.format_exc()}")
            return f"Winds aloft unavailable: {e}"

    print(f"  [Analyzer] Running supplementary checks in parallel...")
    with ThreadPoolExecutor(max_workers=5) as _pool:
        _f_pireps  = _pool.submit(_run_pireps)
        _f_sigmets = _pool.submit(_run_sigmets)
        _f_route   = _pool.submit(_run_route_weather)
        _f_cwind   = _pool.submit(_run_crosswind)
        _f_winds   = _pool.submit(_run_winds_aloft)

        pireps_result        = _f_pireps.result()
        sigmet_result        = _f_sigmets.result()
        route_weather_result = _f_route.result()
        crosswind_result     = _f_cwind.result()
        winds_aloft_result   = _f_winds.result()

    print(f"  [Analyzer] Supplementary checks complete")

    # Score destination risk
    risk_result = ""
    destination_is_unusable = False
    reason_unusable = None

    if dest_metar:
        risk_result = score_risk_tool.invoke({
            "metar_json": dest_metar,
            "taf_json": dest_taf,
            "notams_text": dest_notams,
            "is_ifr_rated": state.get("is_ifr") or False,
            "is_night": state.get("is_night") or False,
        })
        print(f"  [Analyzer] Risk result:\n{risk_result}")

        # Parse verdict from risk output
        if "NO-GO" in risk_result or "EXTREME" in risk_result:
            destination_is_unusable = True
            reason_unusable = _extract_reason(risk_result)
    else:
        destination_is_unusable = True
        reason_unusable = f"No weather data available for {destination}"

    # Fuel calculation if we have aircraft parameters
    fuel_result = ""
    if all([
        state.get("fuel_onboard_gal"),
        state.get("fuel_burn_gph"),
        state.get("true_airspeed_kts"),
    ]):
        from app.airport_db import get_airport

        dist = _estimate_distance(departure, destination)
        if dist:
            fuel_result = calculate_fuel_tool.invoke({
                "distance_nm": dist,
                "fuel_onboard_gal": state["fuel_onboard_gal"],
                "fuel_burn_gph": state["fuel_burn_gph"],
                "true_airspeed_kts": state["true_airspeed_kts"],
                "is_ifr": state.get("is_ifr") or False,
                "is_night": state.get("is_night") or False,
                "alternate_distance_nm": 0,
            })
            print(f"  [Analyzer] Fuel result:\n{fuel_result}")

    # Night currency check
    night_currency_result = ""
    departure_offset = state.get("departure_offset_minutes")
    is_night_current = state.get("is_night_current")

    if departure_offset is not None or is_night_current is not None:
        from datetime import datetime, timezone, timedelta

        # Always calculate departure_time before using it
        now = datetime.now(timezone.utc)
        offset = departure_offset if departure_offset is not None else 0
        departure_time = now + timedelta(minutes=offset)

        # Estimate flight time from fuel calc
        flight_time_min = 60.0
        if state.get("fuel_analysis"):
            for line in state["fuel_analysis"].splitlines():
                if "Flight time:" in line:
                    try:
                        flight_time_min = float(
                            line.split(":")[1].strip().split()[0]
                        )
                    except (ValueError, IndexError):
                        pass

        night_currency_result = check_night_currency_tool.invoke({
            "icao": destination,
            "departure_time_utc": departure_time.isoformat(),
            "flight_time_minutes": flight_time_min,
            "is_night_current": is_night_current if is_night_current is not None else False,
            "carrying_passengers": state.get("carrying_passengers") or False,
        })

        print(f"  [Analyzer] Night currency:\n{night_currency_result}")

        if "WARNING" in night_currency_result:
            print("  [Analyzer] Night currency warning flagged")

    return {
        "departure_metar":          dep_metar,
        "departure_taf":            dep_taf,
        "departure_notams":         dep_notams,
        "destination_metar":        dest_metar,
        "destination_taf":          dest_taf,
        "destination_notams":       dest_notams,
        "pireps":                   pireps_result,
        "sigmets":                  sigmet_result,
        "risk_assessment":          risk_result,
        "crosswind_analysis":       crosswind_result,
        "winds_aloft":              winds_aloft_result,
        "fuel_analysis":            fuel_result,
        "destination_is_unusable":  destination_is_unusable,
        "reason_unusable":          reason_unusable,
        "night_currency_check":     night_currency_result,
        "route_weather":            route_weather_result,
    }


def _extract_reason(risk_text: str) -> str:
    """Pull the first CRITICAL or WARNING factor from risk output."""
    for line in risk_text.splitlines():
        if "CRITICAL" in line or "WARNING" in line:
            return line.strip().lstrip("- ")
    return "High risk conditions at destination"


def _estimate_distance(dep_icao: str, dest_icao: str) -> float | None:
    from app.airport_db import get_airport, _haversine_nm
    dep = get_airport(dep_icao)
    dest = get_airport(dest_icao)
    if dep and dest:
        return _haversine_nm(
            dep["lat"], dep["lon"],
            dest["lat"], dest["lon"]
        )
    return None