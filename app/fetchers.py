import httpx
import json
from tenacity import retry, stop_after_attempt, wait_exponential
from app.models import MetarData, TafData, TafPeriod, NotamData
from app.config import settings
from app.cache import get_cached, set_cached

AVWX_BASE = "https://aviationweather.gov/api/data"

# These keywords in a NOTAM text flag it as critical — always include
CRITICAL_KEYWORDS = [
    "CLSD", "CLOSED", "OUT OF SERVICE", "UNSERVICEABLE", "U/S",
    "ILS", "LOC", "GS", "GLIDE", "VASI", "PAPI",
    "RWY", "RUNWAY", "THRESHOLD", "DISPLACED",
    "HAZARD", "OBSTACLE", "CRANE",
]

# These keywords make a NOTAM relevant but not critical
RELEVANT_KEYWORDS = [
    "APCH", "APPROACH", "DEPARTURE", "SID", "STAR",
    "TWY", "TAXIWAY", "APRON",
    "LIGHT", "LGTD", "PCL",
    "FUEL", "AVGAS", "JET-A",
    "ATIS", "ASOS", "AWOS",
    "TFR", "AIRSPACE", "CLASS",
    "WIND", "WX", "WEATHER",
]

# These keywords mean we can safely ignore the NOTAM
IGNORABLE_KEYWORDS = [
    "CRANE" ,           # construction cranes far from field
    "SURVEY",           # land survey activity
    "PARACHUTE",        # unless near your route
    "MODEL AIRCRAFT",
    "FIREWORKS",
]


# ── Helpers ────────────────────────────────────────────────────────────────

def _to_iso(value) -> str | None:
    """Convert Unix timestamp or passthrough ISO string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return str(value)   # already a string — pass through as-is

def _derive_flight_category(
    visibility_sm: float | None,
    ceiling_ft: int | None,
    ceiling_coverage: str | None
) -> str:
    """
    Derive FAA flight category from visibility and ceiling.
    Only BKN and OVC count as ceiling-defining layers.
    """
    has_ceiling = ceiling_coverage in ("BKN", "OVC") and ceiling_ft is not None
    ceil = ceiling_ft if has_ceiling else 99999
    vis = visibility_sm if visibility_sm is not None else 99.0

    if ceil < 500 or vis < 1:
        return "LIFR"
    elif ceil < 1000 or vis < 3:
        return "IFR"
    elif ceil < 3000 or vis < 5:
        return "MVFR"
    else:
        return "VFR"


def _parse_metar(raw: dict) -> MetarData:
    ceiling_ft = None
    ceiling_coverage = None

    # API returns a nested clouds array, not flat cldCvg1/cldBas1 fields
    for cloud in raw.get("clouds", []):
        coverage = cloud.get("cover")
        base = cloud.get("base")
        if coverage in ("BKN", "OVC") and base is not None:
            ceiling_ft = int(base)   # already in feet, not hundreds
            ceiling_coverage = coverage
            break

    visibility = raw.get("visib")
    if isinstance(visibility, str):
        if visibility == "":
            visibility = None
        if visibility is not None:
             # Handle "10+" which the API sometimes returns
            visibility = float(visibility.replace("+", ""))
    elif visibility is not None:
        visibility = float(visibility)

    flight_cat = _derive_flight_category(visibility, ceiling_ft, ceiling_coverage)

    return MetarData(
        icao=raw.get("icaoId", ""),
        raw=raw.get("rawOb", ""),
        visibility_sm=visibility,
        ceiling_ft=ceiling_ft,
        ceiling_coverage=ceiling_coverage,
        wind_dir=raw.get("wdir"),
        wind_speed_kts=raw.get("wspd"),
        wind_gust_kts=raw.get("wgst"),
        weather=raw.get("wxString"),
        temp_c=raw.get("temp"),
        altimeter=raw.get("altim"),
        flight_category=flight_cat,
        observed_time=_to_iso(raw.get("obsTime")),
    )


def _parse_taf(raw: dict) -> TafData:
    """Parse one raw TAF dict from the API into our clean model."""
    periods = []
    for fcst in raw.get("fcsts", []):
        ceiling_ft = None
        ceiling_coverage = None

        for cloud in fcst.get("clouds", []):
            coverage = cloud.get("cover")
            base = cloud.get("base")
            if coverage in ("BKN", "OVC") and base is not None:
                ceiling_ft = int(base)
                ceiling_coverage = coverage
                break

        vis = fcst.get("visib")
        if vis is not None and str(vis).strip():
            vis = int(str(vis).replace("+", ""))
        else:
            vis = None

        wind_dir = fcst.get("wdir")
        if wind_dir is not None:
            try:
                wind_dir = int(wind_dir)
            except (ValueError, TypeError):
                wind_dir = str(wind_dir)  # keeps "VRB" or "270V340" as-is

        periods.append(TafPeriod(
            time_from=_to_iso(fcst.get("timeFrom")),
            time_to=_to_iso(fcst.get("timeTo")),
            wind_dir=wind_dir,
            wind_speed_kts=fcst.get("wspd"),
            wind_gust_kts=fcst.get("wgst"),
            visibility_sm=vis,
            ceiling_ft=ceiling_ft,
            ceiling_coverage=ceiling_coverage,
            weather=fcst.get("wxString"),
            change_type=fcst.get("changeType"),
        ))

    return TafData(
        icao=raw.get("icaoId", ""),
        raw=raw.get("rawTAF", ""),
        issued_time=_to_iso(raw.get("issueTime")),
        valid_from=_to_iso(raw.get("validTimeFrom")),
        valid_to=_to_iso(raw.get("validTimeTo")),
        forecast_periods=periods,
    )

def _categorize_notam(text: str) -> tuple[str, bool]:
    """
    Returns (category, is_critical) for a NOTAM based on its text.
    Category is a short human-readable label.
    """
    upper = text.upper()

    if any(k in upper for k in ["RWY", "RUNWAY", "THRESHOLD", "DISPLACED"]):
        category = "RWY"
    elif any(k in upper for k in ["ILS", "LOC", "GS", "GLIDE", "VASI", "PAPI"]):
        category = "NAV"
    elif any(k in upper for k in ["TWY", "TAXIWAY", "APRON"]):
        category = "TWY"
    elif any(k in upper for k in ["APCH", "APPROACH", "SID", "STAR"]):
        category = "APCH"
    elif any(k in upper for k in ["LIGHT", "LGTD", "PCL"]):
        category = "LGTG"
    elif any(k in upper for k in ["FUEL", "AVGAS", "JET-A"]):
        category = "FUEL"
    elif any(k in upper for k in ["TFR", "AIRSPACE", "CLASS"]):
        category = "AIRSPACE"
    elif any(k in upper for k in ["ATIS", "ASOS", "AWOS"]):
        category = "COM"
    else:
        category = "GEN"

    is_critical = any(
        k in upper for k in ["CLSD", "CLOSED", "OUT OF SERVICE", "UNSERVICEABLE", "U/S"]
    )

    return category, is_critical


def _is_relevant(text: str) -> bool:
    """
    Returns True if a NOTAM is worth including in the briefing.
    Filters out purely administrative or distant NOTAMs.
    """
    upper = text.upper()
    has_critical = any(k in upper for k in CRITICAL_KEYWORDS)
    has_relevant = any(k in upper for k in RELEVANT_KEYWORDS)
    return has_critical or has_relevant


def _parse_notam(raw: dict) -> NotamData | None:
    """Parse one raw NOTAM dict from the FAA API."""
    try:
        core = raw.get("coreNOTAMData", {})
        notam = core.get("notam", {})

        text = notam.get("text", "")
        if not text:
            return None

        if not _is_relevant(text):
            return None

        category, is_critical = _categorize_notam(text)

        # Pull translated text if available
        translated = None
        translations = core.get("notamTranslation", [])
        if translations:
            translated = translations[0].get("simpleText")

        return NotamData(
            notam_id=notam.get("number", "UNKNOWN"),
            location=notam.get("location", ""),
            effective_start=notam.get("effectiveStart"),
            effective_end=notam.get("effectiveEnd"),
            raw_text=text,
            translated_text=translated,
            category=category,
            is_critical=is_critical,
        )
    except Exception:
        return None
    
async def get_metar(icao: str) -> MetarData | None:
    """
    Cache-aware METAR fetch.
    Checks DB first, falls back to live API if stale or missing.
    """
    key = f"metar:{icao.upper()}"
    cached = get_cached(key, ttl_minutes=settings.weather_cache_ttl_minutes)

    if cached:
        print(f"  [CACHE] METAR hit: {icao}")
        return MetarData(**json.loads(cached))

    print(f"  [API]   METAR fetch: {icao}")
    result = await fetch_metar(icao)

    if result:
        set_cached(key, result.model_dump_json())

    return result


async def get_taf(icao: str) -> TafData | None:
    """
    Cache-aware TAF fetch with nearby airport fallback.
    """
    key = f"taf:{icao.upper()}"
    cached = get_cached(key, ttl_minutes=settings.weather_cache_ttl_minutes)

    if cached:
        print(f"  [CACHE] TAF hit: {icao}")
        return TafData(**json.loads(cached))

    print(f"  [API]   TAF fetch: {icao}")
    result = await fetch_taf(icao)

    # Fallback to nearest airport with a TAF
    if result is None:
        result = await fetch_nearest_taf(icao)

    if result:
        set_cached(key, result.model_dump_json())

    return result

async def fetch_nearest_taf(icao: str, radius_nm: float = 15) -> TafData | None:
    """
    Fetch TAF for the nearest airport that has one within radius_nm.
    Used as fallback when the requested airport has no TAF.
    """
    from app.airport_db import find_alternates, get_airport

    candidates = find_alternates(icao, radius_nm=radius_nm, min_runway_ft=3000)

    for candidate_icao in candidates:
        taf = await fetch_taf(candidate_icao)
        if taf:
            print(f"  [TAF] {icao} has no TAF — using {candidate_icao} "
                  f"(within {radius_nm}nm)")
            # Tag the TAF so downstream knows it's a proxy
            taf.icao = f"{candidate_icao} (proxy for {icao})"
            return taf

    return None


# ── Public fetch functions ──────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def fetch_metar(icao: str) -> MetarData | None:
    params = {
        "ids": icao.upper(),
        "format": "json",
        "hours": 2,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{AVWX_BASE}/metar", params=params)
        response.raise_for_status()

        if not response.text.strip():
            return None

        data = response.json()

    if not data:
        return None

    return _parse_metar(data[0])


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def fetch_taf(icao: str) -> TafData | None:
    params = {
        "ids": icao.upper(),
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{AVWX_BASE}/taf", params=params)
        response.raise_for_status()

        if not response.text.strip():
            return None

        data = response.json()

    if not data:
        return None

    return _parse_taf(data[0])

async def fetch_notams(icao: str) -> list:
    print(f"  [NOTAM] {icao}: skipping (credentials pending)")
    return []

"""@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def fetch_notams(icao: str) -> list[NotamData]:

    if not settings.faa_client_id or not settings.faa_client_secret:
        print(f"  [NOTAM] No FAA credentials — skipping {icao}")
        return []

    headers = {
        "client_id": settings.faa_client_id,
        "client_secret": settings.faa_client_secret,
    }
    params = {
        "icaoLocation": icao.upper(),
        "pageSize": 50,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            "https://external-api.faa.gov/notamapi/v1/notams",
            headers=headers,
            params=params,
        )
        response.raise_for_status()

        if not response.text.strip():
            return []

        data = response.json()

    raw_notams = data.get("items", [])
    total = data.get("totalCount", 0)

    parsed = []
    for raw in raw_notams:
        notam = _parse_notam(raw)
        if notam:
            parsed.append(notam)

    # Sort: critical first, then by category
    parsed.sort(key=lambda n: (not n.is_critical, n.category))

    print(f"  [NOTAM] {icao}: {len(parsed)} relevant of {total} total")
    return parsed """