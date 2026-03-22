import asyncio
from app.state import BriefingState
from app.fetchers import get_metar, get_taf, fetch_notams
from app.tools.risk import score_risk_tool
from app.tools.fuel import calculate_fuel_tool
from app.tools.sunset import check_night_currency_tool
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

    print(f"  [Analyzer] Fetching data for {departure} and {destination}")

    # Fetch both airports concurrently
    dep_metar, dep_taf, dep_notams = asyncio.run(
        _fetch_airport_data(departure)
    )
    dest_metar, dest_taf, dest_notams = asyncio.run(
        _fetch_airport_data(destination)
    )

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
            "is_night_current": is_night_current
                if is_night_current is not None else True,
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
        "risk_assessment":          risk_result,
        "fuel_analysis":            fuel_result,
        "destination_is_unusable":  destination_is_unusable,
        "reason_unusable":          reason_unusable,
        "night_currency_check": night_currency_result,
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