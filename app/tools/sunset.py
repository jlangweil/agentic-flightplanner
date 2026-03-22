from datetime import datetime, timezone, timedelta
from langchain.tools import tool
from pydantic import BaseModel, Field
from astral import LocationInfo
from astral.sun import sun
from app.airport_db import get_airport


class SunsetInput(BaseModel):
    icao: str = Field(
        description="ICAO code of the destination airport"
    )
    departure_time_utc: str = Field(
        description=(
            "Estimated departure time in ISO format UTC. "
            "e.g. '2026-03-21T21:00:00+00:00'"
        )
    )
    flight_time_minutes: float = Field(
        description="Estimated flight time in minutes"
    )
    is_night_current: bool = Field(
        default=True,
        description=(
            "Whether the pilot has logged 3 takeoffs and 3 full-stop "
            "landings within the past 90 days during the period 1 hour "
            "after sunset to 1 hour before sunrise per FAR 61.57(b)"
        )
    )
    carrying_passengers: bool = Field(
        default=False,
        description=(
            "Whether the flight will carry passengers. "
            "Night currency requirement only applies when carrying passengers."
        )
    )


def get_sunset_utc(icao: str, date: datetime) -> datetime | None:
    """Get sunset time in UTC for an airport's location."""
    airport = get_airport(icao)
    if not airport:
        return None

    loc = LocationInfo(
        name=icao,
        region="",
        timezone="UTC",
        latitude=airport["lat"],
        longitude=airport["lon"],
    )

    try:
        s = sun(loc.observer, date=date, tzinfo=timezone.utc)
        return s["sunset"]
    except Exception:
        return None


def get_civil_twilight_end_utc(icao: str, date: datetime) -> datetime | None:
    """
    Get end of civil twilight in UTC.
    FAA defines night as beginning at end of evening civil twilight.
    """
    airport = get_airport(icao)
    if not airport:
        return None

    loc = LocationInfo(
        name=icao,
        region="",
        timezone="UTC",
        latitude=airport["lat"],
        longitude=airport["lon"],
    )

    try:
        s = sun(loc.observer, date=date, tzinfo=timezone.utc)
        return s["dusk"]    # civil twilight end = FAA night begins
    except Exception:
        return None
    
# Format times for display — define early so all code below can use it
def fmt(dt: datetime) -> str:
    if dt is None:
        return "N/A"
    return dt.strftime("%H:%MZ")


@tool("check_night_currency", args_schema=SunsetInput)
def check_night_currency_tool(
    icao: str,
    departure_time_utc: str,
    flight_time_minutes: float,
    is_night_current: bool = True,
    carrying_passengers: bool = False,
) -> str:
    """
    Check whether a flight will require night currency at the destination.
    Calculates sunset and civil twilight end at the destination airport,
    estimates arrival time, and determines if the pilot needs to be night current.
    Per FAR 61.57, night is defined as the period from end of evening civil
    twilight to beginning of morning civil twilight.
    Always call this when the pilot mentions night currency or departure timing.
    """
    # Parse departure time
    try:
        if departure_time_utc.endswith("Z"):
            departure_time_utc = departure_time_utc.replace("Z", "+00:00")
        departure_dt = datetime.fromisoformat(departure_time_utc)
        if departure_dt.tzinfo is None:
            departure_dt = departure_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return f"ERROR: Could not parse departure time: {departure_time_utc}"

    # Calculate ETA
    eta = departure_dt + timedelta(minutes=flight_time_minutes)

    def fmt(dt: datetime) -> str:
        if dt is None:
            return "N/A"
        return dt.strftime("%H:%MZ")

    # Get sunset and sunrise at destination on the ETA date
    airport = get_airport(icao)
    if not airport:
        return f"ERROR: Airport {icao} not found in database"

    loc = LocationInfo(
        name=icao,
        region="",
        timezone="UTC",
        latitude=airport["lat"],
        longitude=airport["lon"],
    )

    try:
        s_eta = sun(loc.observer, date=eta, tzinfo=timezone.utc)
        sunset_eta   = s_eta["sunset"]
        sunrise_eta  = s_eta["sunrise"]
    except Exception as e:
        return f"ERROR: Could not calculate sun times for {icao}: {e}"

    # FAR 61.57(b) night window: 1 hour after sunset to 1 hour before sunrise
    far_night_start = sunset_eta  + timedelta(hours=1)
    far_night_end   = sunrise_eta - timedelta(hours=1)

    # Handle case where sunrise is next day
    # If far_night_end < far_night_start it means we crossed midnight
    in_far_night_window = (
        eta >= far_night_start or eta <= far_night_end
    )

    lines = [
        f"Night Currency Check — {icao} (FAR 61.57(b))",
        f"  Departure (UTC):          {fmt(departure_dt)}",
        f"  Flight time:              {int(flight_time_minutes)} min",
        f"  ETA (UTC):                {fmt(eta)}",
        f"  Sunset (UTC):             {fmt(sunset_eta)}",
        f"  Sunrise (UTC):            {fmt(sunrise_eta)}",
        f"  FAR night window:         {fmt(far_night_start)} – {fmt(far_night_end)}",
        f"  Carrying passengers:      {carrying_passengers}",
        f"  Pilot night current:      {is_night_current}",
        "",
    ]

    if in_far_night_window:
        lines.append(f"  RESULT: Landing falls within FAR 61.57(b) night window")
        lines.append(
            f"  (1 hour after sunset to 1 hour before sunrise)"
        )

        if not carrying_passengers:
            lines += [
                "",
                "  INFO: Solo flight — FAR 61.57(b) currency requirement",
                "  does not apply. No restriction.",
            ]
        elif not is_night_current:
            lines += [
                "",
                "  WARNING: Carrying passengers and pilot is NOT night current.",
                "  FAR 61.57(b) requires 3 takeoffs and 3 full-stop landings",
                "  within the past 90 days during this time period.",
                "  Flight with passengers is not legal per FAR 61.57(b).",
                "  Options: fly solo, depart earlier, or verify currency.",
            ]
        else:
            lines += [
                "",
                "  Pilot is night current — passenger carry is legal.",
            ]
    else:
        margin = (far_night_start - eta).total_seconds() / 60
        lines += [
            f"  RESULT: Landing outside FAR 61.57(b) night window",
            f"  ETA is {int(margin)} min before night window begins",
            f"  Night currency not required",
        ]

    return "\n".join(lines)