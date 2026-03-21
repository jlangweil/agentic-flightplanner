import json
from app.startup import initialize
from app.tools.weather import get_metar_tool, get_taf_tool, get_notams_tool
from app.tools.risk import score_risk_tool
from app.tools.fuel import calculate_fuel_tool
from app.fetchers import get_metar, get_taf
from app.tools.alternates import suggest_alternates_tool
import asyncio

def main():
    initialize()

    print("\n--- get_metar_tool ---")
    result = get_metar_tool.invoke({"icao": "KMMU"})
    print(result[:200])

    print("\n--- get_taf_tool ---")
    result = get_taf_tool.invoke({"icao": "KMMU"})
    print(result[:200])

    print("\n--- get_taf_tool (small airport, no TAF expected) ---")
    result = get_taf_tool.invoke({"icao": "KBID"})
    print(result)

    print("\n--- get_notams_tool ---")
    result = get_notams_tool.invoke({"icao": "KMMU"})
    print(result)

    test_risk()
    test_fuel()
    test_alternates()

def test_risk():
    # Fetch live data for a real score
    metar = asyncio.run(get_metar("KMMU"))
    taf = asyncio.run(get_taf("KMMU"))

    print("\n--- Risk score: live KMMU conditions ---")
    result = score_risk_tool.invoke({
        "metar_json": metar.model_dump_json(),
        "taf_json": taf.model_dump_json() if taf else "",
        "notams_text": "",
        "is_ifr_rated": True,
        "is_night": False,
    })
    print(result)

    print("\n--- Risk score: simulated IFR conditions ---")
    bad_metar = {
        "icao": "KBID",
        "raw": "KBID 171453Z 27025G35KT 1/2SM TSRA OVC008",
        "visibility_sm": 0.5,
        "ceiling_ft": 800,
        "ceiling_coverage": "OVC",
        "wind_speed_kts": 25,
        "wind_gust_kts": 35,
        "weather": "TSRA",
        "flight_category": "LIFR",
    }
    result = score_risk_tool.invoke({
        "metar_json": json.dumps(bad_metar),
        "taf_json": "",
        "notams_text": "KBID ILS RWY 10 LOC UNSERVICEABLE",
        "is_ifr_rated": True,
        "is_night": False,
    })
    print(result)

def test_fuel():
    print("\n--- Fuel: VFR day, KMMU to KBID ---")
    result = calculate_fuel_tool.invoke({
        "distance_nm": 150,
        "fuel_onboard_gal": 40,
        "fuel_burn_gph": 10,
        "true_airspeed_kts": 120,
        "is_ifr": False,
        "is_night": False,
        "alternate_distance_nm": 0,
    })
    print(result)

    print("\n--- Fuel: IFR with alternate ---")
    result = calculate_fuel_tool.invoke({
        "distance_nm": 150,
        "fuel_onboard_gal": 40,
        "fuel_burn_gph": 10,
        "true_airspeed_kts": 120,
        "is_ifr": True,
        "is_night": False,
        "alternate_distance_nm": 45,
    })
    print(result)

    print("\n--- Fuel: insufficient, should NO-GO ---")
    result = calculate_fuel_tool.invoke({
        "distance_nm": 300,
        "fuel_onboard_gal": 30,
        "fuel_burn_gph": 10,
        "true_airspeed_kts": 120,
        "is_ifr": False,
        "is_night": False,
        "alternate_distance_nm": 0,
    })
    print(result)

def test_alternates():
    print("\n--- Alternates for KBID, 75nm radius ---")
    result = suggest_alternates_tool.invoke({
        "destination_icao": "KBID",
        "reason": "Below VFR minimums",
        "radius_nm": 75,
        "min_runway_ft": 3000,
    })
    print(result)

    print("\n--- Alternates for KMMU, 50nm radius ---")
    result = suggest_alternates_tool.invoke({
        "destination_icao": "KMMU",
        "reason": "Runway closed",
        "radius_nm": 50,
        "min_runway_ft": 3000,
    })
    print(result)

if __name__ == "__main__":
    main()