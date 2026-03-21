from langchain.tools import tool
from pydantic import BaseModel, Field


class FuelInput(BaseModel):
    distance_nm: float = Field(
        description="Total flight distance in nautical miles"
    )
    fuel_onboard_gal: float = Field(
        description="Total usable fuel on board in gallons"
    )
    fuel_burn_gph: float = Field(
        description="Aircraft fuel burn rate in gallons per hour"
    )
    true_airspeed_kts: float = Field(
        description="Aircraft true airspeed in knots"
    )
    is_ifr: bool = Field(
        description="True if flying IFR, False if VFR"
    )
    is_night: bool = Field(
        default=False,
        description="True if flying at night. Affects VFR reserve requirement."
    )
    alternate_distance_nm: float = Field(
        default=0.0,
        description=(
            "Distance from destination to alternate airport in nautical miles. "
            "Required for IFR if destination weather is below alternate minimums."
        )
    )


@tool("calculate_fuel", args_schema=FuelInput)
def calculate_fuel_tool(
    distance_nm: float,
    fuel_onboard_gal: float,
    fuel_burn_gph: float,
    true_airspeed_kts: float,
    is_ifr: bool,
    is_night: bool = False,
    alternate_distance_nm: float = 0.0,
) -> str:
    """
    Calculate fuel requirements and margins for a flight under FAR Part 91.
    Returns flight time, fuel required, reserve required, total needed,
    fuel margin, and whether the flight is within fuel limits.
    Use this after determining the route and flight conditions.
    Always call this before issuing a GO recommendation.
    """
    # Flight time to destination
    flight_time_hrs = distance_nm / true_airspeed_kts

    # Fuel to destination
    fuel_to_dest = flight_time_hrs * fuel_burn_gph

    # Reserve requirements per FAR 91
    if is_ifr:
        reserve_minutes = 45
    elif is_night:
        reserve_minutes = 45
    else:
        reserve_minutes = 30

    reserve_fuel = (reserve_minutes / 60) * fuel_burn_gph

    # Alternate fuel (IFR only)
    alternate_fuel = 0.0
    alternate_time_hrs = 0.0
    if is_ifr and alternate_distance_nm > 0:
        alternate_time_hrs = alternate_distance_nm / true_airspeed_kts
        alternate_fuel = alternate_time_hrs * fuel_burn_gph

    # Totals
    total_required = fuel_to_dest + alternate_fuel + reserve_fuel
    fuel_margin = fuel_onboard_gal - total_required
    margin_minutes = (fuel_margin / fuel_burn_gph) * 60 if fuel_burn_gph > 0 else 0
    is_legal = fuel_margin >= 0

    # Endurance — how long can we fly on what we have
    total_endurance_hrs = fuel_onboard_gal / fuel_burn_gph

    result = {
        "flight_time_hrs": round(flight_time_hrs, 2),
        "flight_time_min": round(flight_time_hrs * 60),
        "fuel_to_dest_gal": round(fuel_to_dest, 2),
        "alternate_fuel_gal": round(alternate_fuel, 2),
        "reserve_fuel_gal": round(reserve_fuel, 2),
        "reserve_minutes_required": reserve_minutes,
        "total_fuel_required_gal": round(total_required, 2),
        "fuel_onboard_gal": fuel_onboard_gal,
        "fuel_margin_gal": round(fuel_margin, 2),
        "fuel_margin_minutes": round(margin_minutes),
        "total_endurance_hrs": round(total_endurance_hrs, 2),
        "is_legal": is_legal,
        "rule_applied": "FAR 91 IFR" if is_ifr else f"FAR 91 VFR {'night' if is_night else 'day'}",
        "verdict": "FUEL OK" if is_legal else "FUEL INSUFFICIENT — NO-GO",
    }

    # Format as readable string for the agent
    lines = [
        f"Fuel Analysis ({result['rule_applied']})",
        f"  Flight time:      {result['flight_time_min']} min",
        f"  Fuel to dest:     {result['fuel_to_dest_gal']} gal",
    ]
    if alternate_fuel > 0:
        lines.append(
            f"  Alternate fuel:   {result['alternate_fuel_gal']} gal "
            f"({round(alternate_time_hrs * 60)} min)"
        )
    lines += [
        f"  Reserve required: {result['reserve_fuel_gal']} gal "
        f"({reserve_minutes} min)",
        f"  Total required:   {result['total_fuel_required_gal']} gal",
        f"  Fuel on board:    {result['fuel_onboard_gal']} gal",
        f"  Margin:           {result['fuel_margin_gal']} gal "
        f"({result['fuel_margin_minutes']} min)",
        f"  Endurance:        {result['total_endurance_hrs']} hrs",
        f"  Status:           {result['verdict']}",
    ]
    return "\n".join(lines)