import httpx
import json
from tenacity import retry, stop_after_attempt, wait_exponential
from app.models import MetarData, TafData, TafPeriod, NotamData, PirepData, WindsAloftStation, MosPeriod, MosData
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
            try:
                vis = float(str(vis).replace("+", ""))
            except ValueError:
                vis = None
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


# ── PIREP helpers ────────────────────────────────────────────────────────────

def _parse_pirep(raw: dict) -> PirepData | None:
    """Parse one raw PIREP dict from the aviationweather.gov API."""
    try:
        raw_ob = raw.get("rawOb", "")
        if not raw_ob:
            return None

        flt_lvl = raw.get("fltLvl")
        altitude_ft = int(flt_lvl) * 100 if flt_lvl is not None else None

        tb_int = raw.get("tbInt1") or raw.get("tbInt2") or None
        tb_base = raw.get("tbBas1")
        tb_top = raw.get("tbTop1")

        ic_int = raw.get("icgInt1") or raw.get("icgInt2") or None
        ic_type = raw.get("icgType1") or raw.get("icgType2") or None
        ic_base = raw.get("icgBas1")
        ic_top = raw.get("icgTop1")

        # Normalize empty strings to None
        tb_int = tb_int if tb_int and tb_int.strip() else None
        ic_int = ic_int if ic_int and ic_int.strip() else None
        ic_type = ic_type if ic_type and ic_type.strip() else None

        return PirepData(
            icao=raw.get("icaoId", ""),
            raw=raw_ob,
            obs_time=_to_iso(raw.get("obsTime")),
            lat=raw.get("lat"),
            lon=raw.get("lon"),
            altitude_ft=altitude_ft,
            aircraft_type=raw.get("acType") or None,
            turbulence_intensity=tb_int,
            turbulence_base_ft=int(tb_base) * 100 if tb_base is not None else None,
            turbulence_top_ft=int(tb_top) * 100 if tb_top is not None else None,
            icing_intensity=ic_int,
            icing_type=ic_type,
            icing_base_ft=int(ic_base) * 100 if ic_base is not None else None,
            icing_top_ft=int(ic_top) * 100 if ic_top is not None else None,
            temp_c=raw.get("temp"),
            wind_dir=raw.get("wdir"),
            wind_speed_kts=raw.get("wspd"),
            visibility_sm=raw.get("visib"),
            wx_string=raw.get("wxString") or None,
            pirep_type=raw.get("pirepType", "PIREP"),
        )
    except Exception:
        return None


async def fetch_pireps(icao: str, radius_nm: int = 100) -> list[PirepData]:
    """Fetch recent PIREPs within radius_nm of an airport. Single attempt."""
    params = {
        "id": icao.upper(),
        "distance": radius_nm,
        "age": 3,
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"{AVWX_BASE}/pirep", params=params)
            response.raise_for_status()
            if not response.text.strip():
                return []
            data = response.json()
        if not data:
            return []
        parsed = []
        for raw in data:
            pirep = _parse_pirep(raw)
            if pirep:
                parsed.append(pirep)
        return parsed
    except Exception as e:
        print(f"  [PIREPs] fetch_pireps({icao}) failed: {e}")
        return []


async def get_pireps(icao: str, radius_nm: int = 100) -> list[PirepData]:
    """Cache-aware PIREPs fetch. TTL: 30 minutes."""
    key = f"pireps:{icao.upper()}:{radius_nm}"
    cached = get_cached(key, ttl_minutes=30)

    if cached:
        print(f"  [CACHE] PIREPs hit: {icao}")
        data = json.loads(cached)
        return [PirepData(**p) for p in data]

    print(f"  [API]   PIREPs fetch: {icao} ({radius_nm}nm)")
    results = await fetch_pireps(icao, radius_nm)

    if results:
        set_cached(key, json.dumps([p.model_dump() for p in results]))

    return results


# ── Winds aloft helpers ──────────────────────────────────────────────────────

def _parse_fd_value(val: str, alt_ft: int) -> tuple[int | None, int | None, float | None] | None:
    """
    Parse one FD winds aloft encoded value.
    Returns (wind_dir_deg, wind_speed_kts, temp_c) or None if unparseable.
    FD encoding:
      - "9900" = light and variable, no temp
      - "DDSS" = direction (DD*10), speed SS kts, no temp (3000 ft)
      - "DDSSsTT" = direction, speed, sign + temp
      - "DDSSTT" = direction, speed, temp (always negative at ≥24000 ft)
      - If DD > 36: DD -= 50, speed += 100 (high speed encoding)
    """
    val = val.strip()
    if not val or val in ("////",):
        return None
    if val == "9900":
        return None, 0, None  # light and variable
    if len(val) < 4:
        return None
    try:
        dd = int(val[:2])
        ss = int(val[2:4])
        # High-speed encoding
        if dd > 36:
            dd = dd - 50
            ss = ss + 100
        wdir = dd * 10
        wspd = ss
        temp: float | None = None
        if len(val) > 4:
            rest = val[4:]
            if rest.startswith("+"):
                temp = float(rest[1:])
            elif rest.startswith("-"):
                temp = -float(rest[1:])
            elif rest.isdigit():
                temp = -float(rest) if alt_ft >= 24000 else float(rest)
        return wdir, wspd, temp
    except (ValueError, IndexError):
        return None


def _parse_fd_text(text: str, icao: str) -> list[WindsAloftStation]:
    """
    Parse FD winds aloft text-format response for a given airport ICAO.
    Uses the 3-char station ID (ICAO minus leading 'K' for US airports).
    Falls back to the geographically nearest FD reporting station if the
    target airport is not itself a reporting station.
    """
    lines = text.splitlines()

    # Locate the FT header line and extract altitude columns
    alt_levels: list[int] = []
    header_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("FT"):
            alt_levels = [int(x) for x in line.split() if x.isdigit()]
            header_idx = i
            break

    if header_idx < 0 or not alt_levels:
        return []

    # Collect all data lines (station + values)
    data_lines: list[str] = []
    for line in lines[header_idx + 1:]:
        stripped = line.strip()
        if stripped and stripped[0].isalpha() and len(stripped.split()) >= 2:
            data_lines.append(stripped)

    if not data_lines:
        return []

    # Try direct 3-char station match first
    station_prefix = icao[1:].upper() if icao.upper().startswith("K") else icao[:3].upper()
    target_line: str | None = None
    for line in data_lines:
        if line.upper().startswith(station_prefix + " ") or line.upper().startswith(station_prefix + "\t"):
            target_line = line
            break

    # Fallback: find nearest FD station by lat/lon using airports.csv
    if target_line is None:
        from app.airport_db import get_airport, _haversine_nm
        target_airport = get_airport(icao)
        if target_airport:
            best_dist = float("inf")
            best_line: str | None = None
            for line in data_lines:
                fd_code = line.split()[0].upper()
                # Most US FD stations are "K" + 3-char code in airports.csv
                candidate = get_airport("K" + fd_code)
                if candidate:
                    dist = _haversine_nm(
                        target_airport["lat"], target_airport["lon"],
                        candidate["lat"], candidate["lon"],
                    )
                    if dist < best_dist:
                        best_dist = dist
                        best_line = line
            if best_line:
                fd_id = best_line.split()[0]
                print(f"  [Winds] No FD data for {icao} — using nearest station {fd_id} ({best_dist:.0f}nm)")
                target_line = best_line

    if not target_line:
        return []

    parts = target_line.split()
    station_id = parts[0]
    values = parts[1:]

    results: list[WindsAloftStation] = []
    for i, val in enumerate(values):
        if i >= len(alt_levels):
            break
        alt_ft = alt_levels[i]
        parsed = _parse_fd_value(val, alt_ft)
        if parsed is not None:
            wdir, wspd, temp = parsed
            results.append(WindsAloftStation(
                station_id=station_id,
                altitude_ft=alt_ft,
                wind_dir=wdir if wspd and wspd > 0 else None,
                wind_speed_kts=wspd,
                temp_c=temp,
            ))

    return results


async def fetch_winds_aloft(icao: str) -> list[WindsAloftStation]:
    """
    Fetch 6-hour FD winds aloft forecast for an airport.
    The API returns fixed-width text (FD format); we parse it directly.
    Single attempt only — this is optional data and should not block the briefing.
    """
    params = {
        "datasource": "WindTemps",
        "stationString": icao.upper(),
        "level": "low",
        "fcst": "06",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"{AVWX_BASE}/windtemp", params=params)
            response.raise_for_status()
            text = response.text
            if not text.strip():
                return []
        return _parse_fd_text(text, icao)
    except Exception as e:
        print(f"  [Winds] fetch_winds_aloft({icao}) failed: {e}")
        return []


async def get_winds_aloft(icao: str) -> list[WindsAloftStation]:
    """Cache-aware winds aloft fetch. TTL: 60 minutes."""
    key = f"winds_aloft:{icao.upper()}"
    cached = get_cached(key, ttl_minutes=60)

    if cached:
        print(f"  [CACHE] Winds aloft hit: {icao}")
        data = json.loads(cached)
        return [WindsAloftStation(**w) for w in data]

    print(f"  [API]   Winds aloft fetch: {icao}")
    results = await fetch_winds_aloft(icao)

    if results:
        set_cached(key, json.dumps([w.model_dump() for w in results]))

    return results


# ── Public alias so tools can import flight-category derivation ──────────────

derive_flight_category = _derive_flight_category


# ── GFS MOS helpers ──────────────────────────────────────────────────────────

# MOS ceiling code -> feet AGL  (code 10 = 5000+ ft, treated as no ceiling)
_MOS_CIG_FT: dict[int, int | None] = {
    0: 200, 1: 300, 2: 500, 3: 800, 4: 1000,
    5: 1500, 6: 2000, 7: 2500, 8: 3000, 9: 4000, 10: None,
}

# MOS visibility code -> representative statute miles
_MOS_VIS_SM: dict[int, float] = {
    0: 0.2, 1: 0.38, 2: 0.63, 3: 0.88,
    4: 1.5, 5: 2.5, 6: 4.0, 7: 5.5, 8: 7.0,
}


def mos_cig_to_ft(code: int | None) -> int | None:
    """Decode MOS ceiling code to feet AGL. Returns None for 'no ceiling' (code 10)."""
    if code is None:
        return None
    return _MOS_CIG_FT.get(code)


def mos_vis_to_sm(code: int | None) -> float | None:
    """Decode MOS visibility code to statute miles."""
    if code is None:
        return None
    return _MOS_VIS_SM.get(code)


def _parse_mos(raw: dict) -> MosData:
    """Parse one entry from the GFS MOS JSON response."""
    periods = []
    for fp in raw.get("forecastPeriod", []):
        periods.append(MosPeriod(
            ftime=str(fp.get("ftime", "")),
            tmp=fp.get("tmp"),
            dpt=fp.get("dpt"),
            wdr=fp.get("wdr"),
            wsp=fp.get("wsp"),
            sky=fp.get("sky"),
            cld=fp.get("cld") or None,
            vis=fp.get("vis"),
            cig=fp.get("cig"),
        ))
    return MosData(
        station_id=str(raw.get("stationId", "")),
        model_time=str(raw.get("modelTime", "")) or None,
        periods=periods,
    )


async def fetch_mos(icao: str) -> MosData | None:
    """
    Fetch GFS MOS forecast for an airport. Single attempt — MOS is optional
    medium-range data; failures should not block the briefing.
    """
    params = {"icaoLocation": icao.upper(), "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{AVWX_BASE}/mos", params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            if not response.text.strip():
                return None
            data = response.json()
        if not data:
            return None
        entry = data[0] if isinstance(data, list) else data
        return _parse_mos(entry)
    except Exception as e:
        print(f"  [MOS] fetch_mos({icao}) failed: {e}")
        return None


async def get_mos(icao: str) -> MosData | None:
    """Cache-aware GFS MOS fetch. TTL: 180 minutes (updates every 6h)."""
    from datetime import datetime, timezone, timedelta

    key = f"mos:{icao.upper()}"
    cached = get_cached(key, ttl_minutes=180)

    if cached:
        print(f"  [CACHE] MOS hit: {icao}")
        raw = json.loads(cached)
        return MosData(**raw)

    print(f"  [API]   MOS fetch: {icao}")
    result = await fetch_mos(icao)

    if result:
        # Staleness guard: discard if model run is more than 12h old
        if result.model_time:
            try:
                model_dt = datetime.strptime(
                    str(result.model_time)[:10].replace("-", ""), "%Y%m%d"
                ).replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - model_dt > timedelta(hours=18):
                    print(f"  [MOS] {icao}: model run is stale, discarding")
                    return None
            except ValueError:
                pass
        set_cached(key, result.model_dump_json())

    return result

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