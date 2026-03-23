import asyncio
import math
from langchain.tools import tool
from pydantic import BaseModel, Field


class WindsAloftInput(BaseModel):
    departure_icao: str = Field(description="ICAO code of the departure airport")
    destination_icao: str = Field(description="ICAO code of the destination airport")
    cruise_altitude_ft: int = Field(
        default=6000,
        description="Planned cruise altitude in feet MSL",
    )
    true_airspeed_kts: float = Field(
        description="Aircraft true airspeed in knots"
    )


def _course_bearing(dep_lat: float, dep_lon: float, dest_lat: float, dest_lon: float) -> float:
    """Calculate initial true course bearing from departure to destination (degrees)."""
    lat1 = math.radians(dep_lat)
    lat2 = math.radians(dest_lat)
    d_lon = math.radians(dest_lon - dep_lon)
    x = math.sin(d_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def _headwind_component(wind_dir: int, wind_speed: int, course: float) -> float:
    """
    Return headwind component in knots (positive = headwind, negative = tailwind).
    """
    angle = math.radians((wind_dir - course + 360) % 360)
    return wind_speed * math.cos(angle)


def _closest_altitude(wind_levels: list, target_ft: int):
    """Return the winds aloft entry closest to target_ft."""
    if not wind_levels:
        return None
    return min(wind_levels, key=lambda w: abs(w.altitude_ft - target_ft))


@tool("get_winds_aloft", args_schema=WindsAloftInput)
def get_winds_aloft_tool(
    departure_icao: str,
    destination_icao: str,
    cruise_altitude_ft: int = 6000,
    true_airspeed_kts: float = 120.0,
) -> str:
    """
    Fetch winds aloft forecast for the route and calculate the headwind/tailwind
    component at cruise altitude. Returns adjusted ground speed and estimated
    impact on flight time and fuel. Call this whenever aircraft TAS is known.
    """
    from app.fetchers import get_winds_aloft as _fetch
    from app.airport_db import get_airport, _haversine_nm

    dep = get_airport(departure_icao)
    dest = get_airport(destination_icao)

    if not dep or not dest:
        return "Winds Aloft: Airport data unavailable — cannot compute wind component"

    course = _course_bearing(dep["lat"], dep["lon"], dest["lat"], dest["lon"])
    distance_nm = _haversine_nm(dep["lat"], dep["lon"], dest["lat"], dest["lon"])

    # Fetch winds aloft for both airports and pick the best match
    try:
        dep_winds = asyncio.run(_fetch(departure_icao))
        dest_winds = asyncio.run(_fetch(destination_icao))
    except Exception as e:
        return f"Winds Aloft: fetch failed — {e}"

    all_winds = dep_winds + dest_winds
    if not all_winds:
        return f"Winds Aloft: No forecast data available for {departure_icao}/{destination_icao}"

    # Find level closest to cruise altitude
    best = _closest_altitude(all_winds, cruise_altitude_ft)
    if best is None or best.wind_dir is None or best.wind_speed_kts is None:
        return (
            f"Winds Aloft — {departure_icao} -> {destination_icao}\n"
            f"  Cruise altitude: {cruise_altitude_ft:,}ft\n"
            f"  No wind data at this altitude — light and variable assumed"
        )

    hw = _headwind_component(best.wind_dir, best.wind_speed_kts, course)
    ground_speed = true_airspeed_kts - hw
    if ground_speed <= 0:
        return (
            f"Winds Aloft — {departure_icao} -> {destination_icao}\n"
            f"  WARNING: Headwind ({hw:.0f}kts) exceeds TAS — flight not feasible at this altitude"
        )

    flight_time_min = (distance_nm / ground_speed) * 60
    no_wind_time_min = (distance_nm / true_airspeed_kts) * 60
    delta_min = flight_time_min - no_wind_time_min

    wind_type = "headwind" if hw > 0 else "tailwind"
    wind_magnitude = abs(hw)

    lines = [
        f"Winds Aloft — {departure_icao} -> {destination_icao}",
        f"  Course:            {course:.0f}deg true",
        f"  Cruise altitude:   {best.altitude_ft:,}ft (nearest available)",
        f"  Wind at altitude:  {best.wind_dir:03d}deg at {best.wind_speed_kts}kts",
    ]
    if best.temp_c is not None:
        lines.append(f"  Temperature:       {best.temp_c:+.0f}degC")

    lines += [
        f"  {wind_type.capitalize()} component: {wind_magnitude:.0f}kts",
        f"  Ground speed:      {ground_speed:.0f}kts (TAS {true_airspeed_kts:.0f}kts)",
        f"  Est. flight time:  {flight_time_min:.0f} min"
        + (f" ({abs(delta_min):.0f} min {'longer' if delta_min > 0 else 'shorter'} than calm)"
           if abs(delta_min) > 1 else ""),
    ]

    if hw > 20:
        lines.append(f"  WARNING: Strong headwind ({hw:.0f}kts) — fuel burn will be higher than planned")
    elif hw < -20:
        lines.append(f"  INFO: Strong tailwind — ground speed and fuel efficiency improved")

    return "\n".join(lines)
