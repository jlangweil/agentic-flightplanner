"""
Airport timezone utilities.

Uses timezonefinder + zoneinfo to convert UTC datetimes to local time
for a given airport ICAO code (via its lat/lon from the airport database).
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import lru_cache

_tf = None


def _get_tf():
    global _tf
    if _tf is None:
        from timezonefinder import TimezoneFinder
        _tf = TimezoneFinder()
    return _tf


@lru_cache(maxsize=512)
def airport_timezone(icao: str) -> str:
    """Return the IANA timezone name for an airport (e.g. 'America/Chicago')."""
    from app.airport_db import get_airport
    airport = get_airport(icao.upper())
    if not airport:
        return "UTC"
    try:
        tz_name = _get_tf().timezone_at(lat=airport["lat"], lng=airport["lon"])
        return tz_name or "UTC"
    except Exception:
        return "UTC"


def to_local(utc_dt: datetime, icao: str) -> datetime:
    """Convert a UTC datetime to local time at the given airport."""
    tz_name = airport_timezone(icao)
    return utc_dt.astimezone(ZoneInfo(tz_name))


def fmt_local(utc_dt: datetime, icao: str) -> str:
    """
    Return a formatted time string showing both local and UTC.
    e.g. "13:53 CDT (18:53Z)"
    """
    local_dt = to_local(utc_dt, icao)
    tz_abbr  = local_dt.strftime("%Z")   # CDT, MDT, PST, etc.
    return f"{local_dt.strftime('%H:%M')} {tz_abbr} ({utc_dt.strftime('%H:%MZ')})"
