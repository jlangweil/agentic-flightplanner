from app.tools.weather import get_metar_tool, get_taf_tool, get_notams_tool
from app.tools.fuel import calculate_fuel_tool
from app.tools.risk import score_risk_tool
from app.tools.alternates import suggest_alternates_tool
from app.tools.sunset import check_night_currency_tool

all_tools = [
    get_metar_tool,
    get_taf_tool,
    get_notams_tool,
    calculate_fuel_tool,
    score_risk_tool,
    suggest_alternates_tool,
    check_night_currency_tool,
]