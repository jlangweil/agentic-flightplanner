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


def get_airport(icao: str) -> dict | None:
    """Return airport data for a single ICAO code."""
    return _load_airports().get(icao.upper())


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