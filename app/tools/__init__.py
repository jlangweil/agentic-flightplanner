from app.tools.weather import get_metar_tool, get_taf_tool, get_notams_tool
from app.tools.fuel import calculate_fuel_tool
from app.tools.risk import score_risk_tool
from app.tools.alternates import suggest_alternates_tool
from app.tools.sunset import check_night_currency_tool
from app.tools.crosswind import check_crosswind_tool
from app.tools.pireps import get_pireps_tool
from app.tools.winds_aloft import get_winds_aloft_tool
from app.tools.sigmet import get_sigmet_tool
from app.tools.route_weather import get_route_weather_tool

all_tools = [
    get_metar_tool,
    get_taf_tool,
    get_notams_tool,
    calculate_fuel_tool,
    score_risk_tool,
    suggest_alternates_tool,
    check_night_currency_tool,
    check_crosswind_tool,
    get_pireps_tool,
    get_winds_aloft_tool,
    get_sigmet_tool,
    get_route_weather_tool,
]