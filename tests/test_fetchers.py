import asyncio
from app.fetchers import fetch_metar, fetch_taf, fetch_notams

async def main():
    for icao in ["KTEB"]:
        print(f"\n{'='*40}")
        print(f"  {icao}")
        print(f"{'='*40}")

        metar = await fetch_metar(icao)
        if metar:
            print(f"  Raw:        {metar.raw}")
            print(f"  Category:   {metar.flight_category}")
            print(f"  Visibility: {metar.visibility_sm} SM")
            print(f"  Ceiling:    {metar.ceiling_ft} ft {metar.ceiling_coverage or ''}")
            print(f"  Wind:       {metar.wind_dir}° @ {metar.wind_speed_kts} kts", end="")
            if metar.wind_gust_kts:
                print(f" gusting {metar.wind_gust_kts}")
            else:
                print()
            print(f"  IFR?        {metar.is_ifr}")
        else:
            print("  No METAR data available")

        taf = await fetch_taf(icao)
        if taf:
            print(f"\n  TAF issued: {taf.issued_time}")
            print(f"  Valid:      {taf.valid_from} → {taf.valid_to}")
            print(f"  Periods:    {len(taf.forecast_periods)}")
            for p in taf.forecast_periods[:3]:   # show first 3
                print(f"    [{p.change_type or 'BASE'}] "
                      f"vis={p.visibility_sm}SM  "
                      f"ceil={p.ceiling_ft}ft {p.ceiling_coverage or ''}")
        else:
            print("  No TAF available (small field)")
        
        # NOTAM test
        notams = await fetch_notams(icao)
        if notams:
            print(f"\n  NOTAMs ({len(notams)} relevant):")
            for n in notams:
                flag = "(!)" if n.is_critical else "   "
                print(f"  {flag} [{n.category}] {n.notam_id}")
                print(f"       {n.raw_text[:80]}...")
        else:
            print("\n  No relevant NOTAMs")

asyncio.run(main())