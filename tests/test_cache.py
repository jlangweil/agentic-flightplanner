import asyncio
from app.startup import initialize
from app.cache import get_cached, set_cached, clear_cache
from app.fetchers import get_metar, get_taf

async def main():
    initialize()

    # Clear any existing cache for clean test
    clear_cache("KTEB")

    print("\n--- First fetch (should hit API) ---")
    metar = await get_metar("KTEB")
    if metar:
        print(f"  {metar.icao}: {metar.flight_category} "
              f"vis={metar.visibility_sm}SM "
              f"ceil={metar.ceiling_ft}ft")

    print("\n--- Second fetch (should hit cache) ---")
    metar2 = await get_metar("KTEB")
    if metar2:
        print(f"  {metar2.icao}: {metar2.flight_category} "
              f"vis={metar2.visibility_sm}SM "
              f"ceil={metar2.ceiling_ft}ft")

    print("\n--- TAF (first fetch) ---")
    taf = await get_taf("KTEB")
    if taf:
        print(f"  Valid: {taf.valid_from} → {taf.valid_to}")
        print(f"  Periods: {len(taf.forecast_periods)}")

    print("\n--- TAF (cached) ---")
    taf2 = await get_taf("KTEB")
    if taf2:
        print(f"  Valid: {taf2.valid_from} → {taf2.valid_to}")

    print("\nDone.")

asyncio.run(main())