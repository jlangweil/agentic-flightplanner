import csv
import math
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
AIRPORTS_CSV = DATA_DIR / "airports.csv"
RUNWAYS_CSV = DATA_DIR / "runways.csv"

# Airport types we consider viable alternates
VIABLE_TYPES = {"large_airport", "medium_airport", "small_airport"}


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate great-circle distance in nautical miles between two points.
    Uses the Haversine formula.
    """
    R_NM = 3440.065  # Earth radius in nautical miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@lru_cache(maxsize=1)
def _load_airports() -> dict[str, dict]:
    """
    Load airports.csv into a dict keyed by ICAO code.
    Cached after first load — only reads the file once per session.
    """
    airports = {}
    with open(AIRPORTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            icao = row.get("ident", "").strip()
            if not icao or len(icao) != 4:
                continue
            try:
                airports[icao] = {
                    "icao": icao,
                    "name": row.get("name", ""),
                    "type": row.get("type", ""),
                    "lat": float(row["latitude_deg"]),
                    "lon": float(row["longitude_deg"]),
                    "elevation_ft": float(row["elevation_ft"] or 0),
                }
            except (ValueError, KeyError):
                continue
    return airports


@lru_cache(maxsize=1)
def _load_max_runways() -> dict[str, int]:
    """
    Load runways.csv and return a dict of icao -> longest_runway_ft.
    Cached after first load.
    """
    runways: dict[str, int] = {}
    with open(RUNWAYS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            icao = row.get("airport_ident", "").strip()
            try:
                length = int(row.get("length_ft") or 0)
            except ValueError:
                continue
            if icao not in runways or length > runways[icao]:
                runways[icao] = length
    return runways


@lru_cache(maxsize=1)
def _load_runway_headings() -> dict[str, list[tuple[str, float]]]:
    """
    Load runways.csv and return a dict of icao -> list of (rwy_id, heading_degT).
    One entry per runway low-end that has a valid heading.
    Crosswind is symmetric for both ends so only le_heading is needed.
    Cached after first load.
    """
    headings: dict[str, list[tuple[str, float]]] = {}
    with open(RUNWAYS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            icao = row.get("airport_ident", "").strip()
            if not icao:
                continue
            le_id = row.get("le_ident", "").strip()
            le_hdg = row.get("le_heading_degT", "").strip()
            if le_id and le_hdg:
                try:
                    if icao not in headings:
                        headings[icao] = []
                    headings[icao].append((le_id, float(le_hdg)))
                except ValueError:
                    pass
    return headings


def get_runway_headings(icao: str) -> list[tuple[str, float]]:
    """Return list of (rwy_id, heading_degT) for all runway ends at an airport."""
    return _load_runway_headings().get(icao.upper(), [])


@lru_cache(maxsize=1)
def _load_all_airports_index() -> tuple[dict[str, dict], dict[str, str]]:
    """
    Load ALL airports (any ident length) and build two secondary indexes:
      - local_code_index: FAA local code (e.g. '42B', '1B1') -> airport dict
      - name_index: lowercase name/municipality tokens -> list of icao codes
    Returns (local_code_index, name_tokens_dict).
    name_tokens_dict maps each searchable word to the airport ident.
    """
    local_code_index: dict[str, dict] = {}
    name_list: list[tuple[str, str, str]] = []   # (ident, name, municipality)

    with open(AIRPORTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ident      = row.get("ident", "").strip()
            local_code = row.get("local_code", "").strip()
            name       = row.get("name", "").strip()
            muni       = row.get("municipality", "").strip()
            try:
                lat = float(row["latitude_deg"])
                lon = float(row["longitude_deg"])
            except (ValueError, KeyError):
                continue

            entry = {
                "icao":       ident,
                "name":       name,
                "type":       row.get("type", ""),
                "lat":        lat,
                "lon":        lon,
                "elevation_ft": float(row.get("elevation_ft") or 0),
                "local_code": local_code,
                "municipality": muni,
            }

            if local_code and len(local_code) <= 5:
                local_code_index[local_code.upper()] = entry
            name_list.append((ident, name, muni))

    return local_code_index, name_list


def get_airport(icao: str) -> dict | None:
    """
    Return airport data for a single ICAO code.
    Falls back to the all-airports local_code index for short FAA codes (e.g. '42B').
    """
    code = icao.upper()
    result = _load_airports().get(code)
    if result:
        return result
    # Try the all-airports index (covers 3-char FAA local codes)
    local_idx, _ = _load_all_airports_index()
    return local_idx.get(code)


def normalize_icao(code: str) -> str:
    """
    Resolve a user-supplied airport code to the best 4-char ICAO we have.

    Rules (in order):
    1. Already a valid 4-char code in our DB → return as-is (uppercased)
    2. 3-char or shorter → try prepending 'K' (e.g. '1B1' → 'K1B1')
    3. Exact match on FAA local_code in the all-airports index → return ident
    4. Return the original uppercased code as fallback
    """
    code = code.strip().upper()
    airports = _load_airports()

    if code in airports:
        return code

    # Try K-prefix for short US codes
    k_code = "K" + code
    if k_code in airports:
        return k_code

    # Try local_code index (covers 42B, 1B1, etc.)
    local_idx, _ = _load_all_airports_index()
    if code in local_idx:
        entry = local_idx[code]
        # Prefer the K-prefix ICAO if available
        k_version = "K" + code
        if k_version in airports:
            return k_version
        return entry["icao"]

    return code


def find_nearest_metar_airport(lat: float, lon: float, max_nm: float = 30.0) -> dict | None:
    """
    Find the nearest airport that has METAR reporting (K-prefix, 4-char ICAO).
    Used as a weather proxy for airports without their own METAR station.
    """
    airports = _load_airports()
    best = None
    best_dist = float("inf")
    for icao, ap in airports.items():
        if not (icao.startswith("K") and len(icao) == 4):
            continue
        dist = _haversine_nm(lat, lon, ap["lat"], ap["lon"])
        if dist < best_dist and dist <= max_nm:
            best_dist = dist
            best = ap
    return best


def find_airport_by_name(query: str, limit: int = 5) -> list[dict]:
    """
    Search airports by name or municipality (case-insensitive substring match).
    Returns up to `limit` results sorted by match quality (exact name first).
    """
    query_lower = query.lower().strip()
    if not query_lower:
        return []

    _, name_list = _load_all_airports_index()
    local_idx, _ = _load_all_airports_index()

    results: list[tuple[int, dict]] = []   # (score, entry)

    # Build a combined lookup: ident -> entry (from local_idx values)
    ident_to_entry: dict[str, dict] = {}
    for entry in local_idx.values():
        ident_to_entry[entry["icao"]] = entry
    # Also add 4-char airports that may not be in local_idx
    for ident, name, muni in name_list:
        if ident not in ident_to_entry:
            ident_to_entry[ident] = {"icao": ident, "name": name, "municipality": muni}

    # Build list of queries to try: full string first, then progressively drop
    # trailing words (handles "Cherry Ridge PA" → "Cherry Ridge", etc.)
    words = query_lower.split()
    candidates_to_try = [query_lower]
    for n in range(len(words) - 1, 0, -1):
        candidates_to_try.append(" ".join(words[:n]))

    seen: set[str] = set()
    for ident, name, muni in name_list:
        if ident in seen:
            continue
        name_lower = name.lower()
        muni_lower = muni.lower()
        for i, q in enumerate(candidates_to_try):
            if q in name_lower or q in muni_lower:
                # Score: exact name match > name starts with > substring;
                # penalise truncated queries slightly so full matches rank first
                if name_lower == q:
                    score = i * 4 + 0
                elif name_lower.startswith(q):
                    score = i * 4 + 1
                elif muni_lower == q:
                    score = i * 4 + 2
                else:
                    score = i * 4 + 3
                entry = ident_to_entry.get(ident, {"icao": ident, "name": name, "municipality": muni})
                results.append((score, entry))
                seen.add(ident)
                break

    results.sort(key=lambda x: x[0])
    return [e for _, e in results[:limit]]


def find_corridor_airports(
    departure_icao: str,
    destination_icao: str,
    corridor_nm: float = 25.0,
    limit: int = 6,
) -> list[dict]:
    """
    Return airports within corridor_nm of the great-circle route between
    departure and destination, excluding the endpoints themselves.

    Uses linear lat/lon interpolation (20 sample points) — accurate enough
    for corridor widths of 25nm over routes up to ~1000nm.

    Returns a list of dicts with keys: icao, name, lat, lon, type, dist_nm,
    route_pos (0=near departure, 1=near destination).
    Sorted by route_pos (departure -> destination order).
    Capped at `limit` results.
    """
    airports = _load_airports()

    dep  = airports.get(departure_icao.upper())
    dest = airports.get(destination_icao.upper())
    if not dep or not dest:
        return []

    endpoints = {departure_icao.upper(), destination_icao.upper()}

    # 20 interpolated waypoints along the route
    N = 20
    waypoints = [
        (
            dep["lat"] + (i / N) * (dest["lat"] - dep["lat"]),
            dep["lon"] + (i / N) * (dest["lon"] - dep["lon"]),
        )
        for i in range(N + 1)
    ]

    candidates = []
    for icao, airport in airports.items():
        if icao in endpoints:
            continue
        if airport["type"] not in VIABLE_TYPES:
            continue
        # Only standard 4-letter K-prefix ICAO codes have METAR/TAF reporting.
        # Private strips (5NJ2, JY43, etc.) have no weather data and slow fetches.
        if not (icao.startswith("K") and len(icao) == 4):
            continue

        a_lat, a_lon = airport["lat"], airport["lon"]

        # Minimum distance to any waypoint on the route
        dists = [_haversine_nm(a_lat, a_lon, wlat, wlon) for wlat, wlon in waypoints]
        min_dist = min(dists)

        if min_dist > corridor_nm:
            continue

        # Route position: index of closest waypoint / N
        closest_idx = dists.index(min_dist)
        candidates.append({
            "icao":      icao,
            "name":      airport["name"],
            "lat":       a_lat,
            "lon":       a_lon,
            "type":      airport["type"],
            "dist_nm":   round(min_dist, 1),
            "route_pos": closest_idx / N,
        })

    candidates.sort(key=lambda x: x["route_pos"])
    return candidates[:limit]


def find_alternates(
    destination_icao: str,
    radius_nm: float = 75,
    min_runway_ft: int = 3000,
    limit: int = 5,
) -> list[str]:
    """
    Find viable alternate airports within radius_nm of the destination.

    Filters by:
    - Airport type (large, medium, small — no heliports or seaplane bases)
    - Minimum runway length
    - US airports only (K prefix) for now

    Returns a list of ICAO codes ordered nearest-to-farthest,
    excluding the destination itself.
    """
    airports = _load_airports()
    runways = _load_max_runways()

    dest = airports.get(destination_icao.upper())
    if dest is None:
        return []

    dest_lat = dest["lat"]
    dest_lon = dest["lon"]

    candidates = []
    for icao, airport in airports.items():
        if icao == destination_icao.upper():
            continue
        if airport["type"] not in VIABLE_TYPES:
            continue
        if not icao.startswith("K"):      # US airports only
            continue
        if runways.get(icao, 0) < min_runway_ft:
            continue

        dist = _haversine_nm(dest_lat, dest_lon, airport["lat"], airport["lon"])
        if dist <= radius_nm:
            candidates.append((icao, dist))

    candidates.sort(key=lambda x: x[1])
    return [icao for icao, _ in candidates[:limit]]