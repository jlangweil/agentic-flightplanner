import json
import asyncio
from datetime import datetime, timezone, timedelta
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from app.state import BriefingState
from app.tools import (
    calculate_fuel_tool, score_risk_tool, suggest_alternates_tool,
    check_night_currency_tool,
    # supplementary tools run deterministically after ReAct
    check_crosswind_tool, get_winds_aloft_tool, get_sigmet_tool,
)
from app.config import settings

# Weather data is pre-fetched in parallel before the ReAct loop.
# The agent only calls the fast computation tools — no network round-trips.
CORE_TOOLS = [
    score_risk_tool,
    calculate_fuel_tool,
    suggest_alternates_tool,
    check_night_currency_tool,
]

REACT_ANALYZER_SYSTEM = """You are an expert aviation weather analyst and
dispatcher conducting a pre-flight safety assessment.

All weather observations (METAR), forecasts (TAF), and NOTAMs have already
been fetched and are provided to you in the prompt below. You do NOT need to
call any weather-fetching tools.

You have access to these computation tools:
- score_flight_risk: risk assessment based on weather and NOTAMs
- calculate_fuel: fuel requirements for the flight
- suggest_alternates: find alternate airports if destination is unusable
- check_night_currency: check if night landing requires currency

Your assessment process:
1. Review the pre-fetched METAR, TAF and NOTAM data provided
2. Call score_flight_risk with the destination METAR JSON and NOTAM text
3. Calculate fuel requirements if aircraft parameters are provided
4. If destination risk is HIGH or EXTREME, call suggest_alternates
5. If departure timing is provided and pilot may need night currency, call check_night_currency

Be thorough. Use the pre-fetched data rather than re-fetching it.
When done, provide a structured summary of your findings."""


def analyzer_react_node(state: BriefingState) -> dict:
    """
    ReAct-based analyzer that dynamically decides which tools to call
    and in what order. Uses only the 7 core tools to stay under the
    LangGraph recursion limit. Supplementary checks (crosswind, PIREPs,
    winds aloft, SIGMETs) are run deterministically after the agent.
    """
    departure   = state["departure_icao"]
    destination = state["destination_icao"]

    if not departure or not destination:
        return {
            "destination_is_unusable": True,
            "reason_unusable": "Could not resolve airport ICAO codes from query",
        }

    # ── Step 1: Pre-fetch all weather data in parallel ────────────────────────
    # This avoids the ReAct agent making sequential network calls (each requiring
    # its own LLM reasoning step). We fetch everything at once and inject it as
    # pre-built context, so the agent only needs to call the fast computation tools.
    print(f"  [ReAct Analyzer] Pre-fetching weather data in parallel...")
    from app.fetchers import get_metar, get_taf, fetch_notams
    from app.airport_db import get_airport, find_nearest_metar_airport, _haversine_nm

    # If destination has no METAR station (e.g. 42B), proxy to nearest reporting airport
    dest_ap = get_airport(destination)
    weather_destination = destination
    weather_proxy_note  = ""
    if dest_ap and not (destination.startswith("K") and len(destination) == 4):
        proxy = find_nearest_metar_airport(dest_ap["lat"], dest_ap["lon"], max_nm=25.0)
        if proxy:
            dist_nm = _haversine_nm(dest_ap["lat"], dest_ap["lon"], proxy["lat"], proxy["lon"])
            weather_destination = proxy["icao"]
            weather_proxy_note = (
                f"NOTE: {destination} ({dest_ap['name']}) has no METAR station. "
                f"Weather proxied from nearest reporting airport: "
                f"{proxy['icao']} ({proxy['name']}, {dist_nm:.0f}nm away)."
            )
            print(f"  [ReAct Analyzer] Weather proxy: {destination} → {weather_destination}")

    async def _prefetch():
        return await asyncio.gather(
            get_metar(departure),
            get_taf(departure),
            fetch_notams(departure),
            get_metar(weather_destination),
            get_taf(weather_destination),
            fetch_notams(destination),          # NOTAMs still for actual destination
        )

    dep_metar_obj, dep_taf_obj, dep_notams_list, \
    dest_metar_obj, dest_taf_obj, dest_notams_list = asyncio.run(_prefetch())
    print(f"  [ReAct Analyzer] Weather data ready")

    # Serialize for state storage
    dep_metar_str  = dep_metar_obj.model_dump_json()  if dep_metar_obj  else ""
    dep_taf_str    = dep_taf_obj.model_dump_json()    if dep_taf_obj    else ""
    dest_metar_str = dest_metar_obj.model_dump_json() if dest_metar_obj else ""
    dest_taf_str   = dest_taf_obj.model_dump_json()   if dest_taf_obj   else ""

    def _fmt_notams(notams) -> str:
        if not notams:
            return "None"
        return "\n".join(
            f"[{n.category}] {n.notam_id}: {n.excerpt or n.raw_text[:120]}"
            for n in notams
        )

    dep_notams_str  = _fmt_notams(dep_notams_list)
    dest_notams_str = _fmt_notams(dest_notams_list)

    # ── Step 2: Build prompt with pre-fetched data injected ───────────────────
    llm = ChatAnthropic(
        model=settings.llm_model,
        api_key=settings.anthropic_api_key,
    )
    react_agent = create_react_agent(llm, CORE_TOOLS)

    from app.timezone_utils import airport_timezone, fmt_local

    now_utc = datetime.now(timezone.utc)

    # Compute departure time and local representations
    offset = state.get("departure_offset_minutes") or 0.0
    dep_time_utc = now_utc + timedelta(minutes=offset)

    dep_tz_name  = airport_timezone(departure)
    dest_tz_name = airport_timezone(destination)

    dep_local_now    = fmt_local(now_utc,      departure)
    dep_local_depart = fmt_local(dep_time_utc, departure)
    dest_local_depart= fmt_local(dep_time_utc, destination)

    # Rough flight time for estimated arrival at destination
    flight_time_min = 60.0  # default; better estimate if we have TAS + distance
    if state.get("true_airspeed_kts"):
        from app.airport_db import _haversine_nm, get_airport
        dep_ap  = get_airport(departure)
        dest_ap = get_airport(destination)
        if dep_ap and dest_ap:
            dist_nm = _haversine_nm(dep_ap["lat"], dep_ap["lon"], dest_ap["lat"], dest_ap["lon"])
            flight_time_min = (dist_nm / state["true_airspeed_kts"]) * 60

    arr_time_utc  = dep_time_utc + timedelta(minutes=flight_time_min)
    dest_local_arr= fmt_local(arr_time_utc, destination)

    flight_info = [
        f"Departure airport: {departure} (timezone: {dep_tz_name})",
        f"Destination airport: {destination} (timezone: {dest_tz_name})",
        f"Current time: {now_utc.strftime('%H:%MZ')} UTC | {dep_local_now} at departure",
        f"Planned departure: {dep_local_depart} at {departure} | {dest_local_depart} at {destination}",
        f"Estimated arrival: {dest_local_arr} at {destination} (approx {flight_time_min:.0f} min flight)",
    ]
    if state.get("fuel_onboard_gal"):
        flight_info.append(
            f"Aircraft: {state['fuel_onboard_gal']} gal onboard, "
            f"{state['fuel_burn_gph']} GPH burn rate, "
            f"{state['true_airspeed_kts']} kts cruise speed"
        )
    flight_info.append("Flight rules: IFR" if state.get("is_ifr") else "Flight rules: VFR")
    if not state.get("is_night_current"):
        flight_info.append("Pilot is NOT night current per FAR 61.57(b)")
    if state.get("carrying_passengers"):
        flight_info.append("Flight is carrying passengers")

    # Format METAR/TAF for human-readable prompt context
    def _metar_summary(obj) -> str:
        if not obj:
            return "No data"
        parts = [f"  Station: {obj.icao}", f"  Category: {obj.flight_category}"]
        if obj.wind_speed_kts is not None:
            parts.append(f"  Wind: {obj.wind_dir or 'VRB'}°/{obj.wind_speed_kts}kts"
                         + (f" G{obj.wind_gust_kts}kts" if obj.wind_gust_kts else ""))
        if obj.visibility_sm is not None:
            parts.append(f"  Visibility: {obj.visibility_sm} SM")
        if obj.ceiling_ft is not None:
            parts.append(f"  Ceiling: {obj.ceiling_ft}ft {obj.ceiling_coverage or ''}")
        if obj.weather:
            parts.append(f"  Weather: {obj.weather}")
        parts.append(f"  [JSON for score_flight_risk: {obj.model_dump_json()}]")
        return "\n".join(parts)

    def _taf_summary(obj, icao: str) -> str:
        """Format TAF periods with local time annotations."""
        if not obj:
            return "No TAF available"
        tz_name = airport_timezone(icao)
        from zoneinfo import ZoneInfo
        zi = ZoneInfo(tz_name)
        lines = [f"  Station: {obj.icao}  (all times shown as local {tz_name} and UTC)"]
        for p in (obj.forecast_periods or [])[:6]:
            # Try to convert the period time to local
            try:
                raw = str(p.time_from).strip()
                if raw and raw != "None":
                    from datetime import datetime as _dt
                    if "T" in raw:
                        period_utc = _dt.fromisoformat(raw.replace("Z","")).replace(tzinfo=timezone.utc)
                    else:
                        period_utc = _dt.strptime(raw[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    period_local = period_utc.astimezone(zi)
                    time_label = (f"{period_local.strftime('%H:%M')} "
                                  f"{period_local.strftime('%Z')} "
                                  f"({period_utc.strftime('%H:%MZ')})")
                else:
                    time_label = str(p.time_from)
            except Exception:
                time_label = str(p.time_from)

            line = f"  From {time_label}"
            if p.ceiling_ft:
                line += f"  Ceil:{p.ceiling_ft}ft {p.ceiling_coverage or ''}"
            if p.visibility_sm is not None:
                line += f"  Vis:{p.visibility_sm}sm"
            if p.weather:
                line += f"  Wx:{p.weather}"
            if p.change_type:
                line += f"  ({p.change_type})"
            lines.append(line)
        return "\n".join(lines)

    proxy_lines = [weather_proxy_note] if weather_proxy_note else []

    query = "\n".join([
        "Conduct a complete pre-flight assessment using the data below.",
        "IMPORTANT: All TAF period times have been annotated with local timezone.",
        "Use LOCAL times when discussing when conditions occur relative to the flight.",
        *proxy_lines,
        "",
        "FLIGHT INFO:",
        *flight_info,
        "",
        f"DEPARTURE ({departure}) METAR:",
        _metar_summary(dep_metar_obj),
        "",
        f"DEPARTURE ({departure}) TAF (times in {dep_tz_name}):",
        _taf_summary(dep_taf_obj, departure),
        "",
        f"DEPARTURE ({departure}) NOTAMs:",
        dep_notams_str,
        "",
        f"DESTINATION ({destination}) METAR"
        + (f" [via proxy {weather_destination}]:" if weather_destination != destination else ":"),
        _metar_summary(dest_metar_obj),
        "",
        f"DESTINATION ({destination}) TAF (times in {dest_tz_name})"
        + (f" [via proxy {weather_destination}]:" if weather_destination != destination else ":"),
        _taf_summary(dest_taf_obj, destination),
        "",
        f"DESTINATION ({destination}) NOTAMs:",
        dest_notams_str,
        "",
        "Now call score_flight_risk with the destination METAR JSON and NOTAMs, "
        "then calculate_fuel if aircraft parameters were provided, "
        "then check_night_currency if departure time is provided, "
        "and suggest_alternates only if risk is HIGH or EXTREME.",
    ])

    print(f"  [ReAct Analyzer] Starting reasoning loop...")

    messages = [
        SystemMessage(content=REACT_ANALYZER_SYSTEM),
        HumanMessage(content=query),
    ]

    from langchain_core.callbacks import BaseCallbackHandler

    class _PrintCallback(BaseCallbackHandler):
        def on_tool_start(self, serialized, input_str, **_):  # noqa: ARG002
            name = serialized.get("name", "tool")
            print(f"  [ReAct] --> {name}()")
        def on_tool_end(self, output, **_):  # noqa: ARG002
            print(f"  [ReAct] <-- done")

    try:
        result = react_agent.invoke(
            {"messages": messages},
            config={"callbacks": [_PrintCallback()], "recursion_limit": 30},
        )
    except Exception as e:
        import traceback
        print(f"  [ReAct Analyzer] ERROR: {e}")
        print(traceback.format_exc())
        return {
            "departure_metar":  dep_metar_str,
            "departure_taf":    dep_taf_str,
            "departure_notams": dep_notams_str,
            "destination_metar":  dest_metar_str,
            "destination_taf":    dest_taf_str,
            "destination_notams": dest_notams_str,
            "destination_is_unusable": True,
            "reason_unusable": f"ReAct analyzer failed: {e}",
            "risk_assessment": f"ERROR: ReAct analyzer crashed -- {e}",
        }

    tool_calls_made = []
    for msg in result["messages"]:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls_made.append(tc["name"])
        elif hasattr(msg, "name") and msg.name:
            print(f"  [ReAct] <-- {msg.name} returned")

    print(f"  [ReAct Analyzer] Tools called: {tool_calls_made}")

    final_response = result["messages"][-1].content
    print(f"  [ReAct Analyzer] Summary:\n{final_response}")

    # Extract core tool results from message history
    updates = _extract_state(result["messages"])

    # Inject the pre-fetched data that the agent didn't need to fetch
    updates["departure_metar"]    = dep_metar_str
    updates["departure_taf"]      = dep_taf_str
    updates["departure_notams"]   = dep_notams_str
    updates["destination_metar"]  = dest_metar_str
    updates["destination_taf"]    = dest_taf_str
    updates["destination_notams"] = dest_notams_str

    # ------------------------------------------------------------------
    # Deterministic risk re-score using pre-fetched data and correct flags.
    # The LLM may pass wrong is_ifr_rated or malformed METAR JSON — we
    # override with an authoritative call here.
    # ------------------------------------------------------------------
    if dest_metar_str:
        from app.tools.risk import score_risk_tool
        risk_result = score_risk_tool.invoke({
            "metar_json":             dest_metar_str,
            "taf_json":               dest_taf_str or "",
            "notams_text":            dest_notams_str or "",
            "is_ifr_rated":           state.get("is_ifr") or False,
            "is_night":               state.get("is_night") or False,
            "ifr_current":            state.get("ifr_current") or False,
            "personal_min_ceiling_ft": state.get("personal_min_ceiling_ft"),
            "personal_min_vis_sm":    state.get("personal_min_vis_sm"),
        })
        print(f"  [ReAct Analyzer] Deterministic risk score:\n{risk_result}")
        updates["risk_assessment"] = risk_result
        if "NO-GO" in risk_result or "EXTREME" in risk_result:
            updates["destination_is_unusable"] = True
            updates["reason_unusable"] = _extract_reason(risk_result)

    # ------------------------------------------------------------------
    # Run supplementary tools deterministically (no recursion risk)
    # ------------------------------------------------------------------
    updates.update(_run_supplementary_tools(state, updates))

    return updates


def _run_supplementary_tools(state: BriefingState, core_updates: dict) -> dict:
    """
    Run crosswind, PIREPs, winds aloft, SIGMETs, and route weather outside
    the ReAct loop so they never count toward the recursion limit.
    All five run in parallel threads to minimise wall-clock time.
    """
    import traceback
    from concurrent.futures import ThreadPoolExecutor

    departure   = state["departure_icao"]
    destination = state["destination_icao"]

    # ── PIREPs ───────────────────────────────────────────────────────────────
    def _run_pireps():
        try:
            print(f"  [ReAct Supp] Fetching PIREPs...")
            from app.fetchers import get_pireps

            async def _both():
                dep_p, dest_p = await asyncio.gather(
                    get_pireps(departure, radius_nm=100),
                    get_pireps(destination, radius_nm=100),
                )
                return dep_p, dest_p

            dep_pireps, dest_pireps = asyncio.run(_both())
            seen: set = set()
            combined = []
            for p in dep_pireps + dest_pireps:
                if p.raw not in seen:
                    seen.add(p.raw)
                    combined.append(p)

            if combined:
                from app.tools.pireps import _rank_pirep, _format_pirep_line, _TURB_RANK, _ICING_RANK
                significant = [p for p in combined if _rank_pirep(p) >= 3]
                routine     = [p for p in combined if _rank_pirep(p) < 3]
                lines = [f"PIREPs -- {departure}->{destination} corridor ({len(combined)} total, last 3h)"]
                if significant:
                    lines.append(f"  SIGNIFICANT ({len(significant)}):")
                    for p in significant[:6]:
                        lines.append(_format_pirep_line(p))
                    if len(significant) > 6:
                        lines.append(f"  ... and {len(significant) - 6} more")
                else:
                    lines.append("  No significant turbulence or icing reports")
                if routine:
                    lines.append(f"  Routine ({len(routine)}):")
                    for p in routine[:3]:
                        lines.append(_format_pirep_line(p))
                if any(_TURB_RANK.get(p.turbulence_intensity or "", 0) >= 4 for p in combined):
                    lines.append("  WARNING: Severe turbulence reported along route")
                if any(_ICING_RANK.get(p.icing_intensity or "", 0) >= 3 for p in combined):
                    lines.append("  WARNING: Moderate or greater icing reported along route")
                result = "\n".join(lines)
            else:
                result = f"PIREPs -- {departure}->{destination}: No recent reports"
            print(f"  [ReAct Supp] PIREPs fetched")
            return "pireps", result
        except Exception as e:
            print(f"  [ReAct Supp] PIREPs ERROR: {e}\n{traceback.format_exc()}")
            return "pireps", f"PIREPs unavailable: {e}"

    # ── SIGMETs ──────────────────────────────────────────────────────────────
    def _run_sigmets():
        try:
            print(f"  [ReAct Supp] Fetching SIGMETs/AIRMETs...")
            result = get_sigmet_tool.invoke({
                "departure_icao": departure,
                "destination_icao": destination,
            })
            print(f"  [ReAct Supp] SIGMETs/AIRMETs fetched")
            return "sigmets", result
        except Exception as e:
            print(f"  [ReAct Supp] SIGMETs ERROR: {e}\n{traceback.format_exc()}")
            return "sigmets", f"SIGMETs/AIRMETs unavailable: {e}"

    # ── Crosswind ─────────────────────────────────────────────────────────────
    def _run_crosswind():
        dest_metar = core_updates.get("destination_metar") or ""
        if not dest_metar:
            return "crosswind_analysis", ""
        try:
            print(f"  [ReAct Supp] Calculating crosswind...")
            dest_wind = json.loads(dest_metar)
            wind_dir   = dest_wind.get("wind_dir")
            wind_speed = dest_wind.get("wind_speed_kts")
            if wind_speed and wind_speed > 0 and wind_dir is not None:
                result = check_crosswind_tool.invoke({
                    "icao": destination,
                    "wind_dir": str(wind_dir),
                    "wind_speed_kts": int(wind_speed),
                    "wind_gust_kts": dest_wind.get("wind_gust_kts"),
                })
                print(f"  [ReAct Supp] Crosswind:\n{result}")
                return "crosswind_analysis", result
            return "crosswind_analysis", ""
        except Exception as e:
            print(f"  [ReAct Supp] Crosswind ERROR: {e}\n{traceback.format_exc()}")
            return "crosswind_analysis", f"Crosswind check unavailable: {e}"

    # ── Route Weather ─────────────────────────────────────────────────────────
    def _run_route_weather():
        try:
            print(f"  [ReAct Supp] Fetching route weather...")
            from app.tools.route_weather import get_route_weather_tool
            result = get_route_weather_tool.invoke({
                "departure_icao": departure,
                "destination_icao": destination,
                "departure_offset_minutes": state.get("departure_offset_minutes") or 0.0,
            })
            print(f"  [ReAct Supp] Route weather fetched")
            return "route_weather", result
        except Exception as e:
            print(f"  [ReAct Supp] Route weather ERROR: {e}\n{traceback.format_exc()}")
            return "route_weather", f"En-route weather unavailable: {e}"

    # ── Winds Aloft ───────────────────────────────────────────────────────────
    def _run_winds_aloft():
        if not state.get("true_airspeed_kts"):
            return "winds_aloft", ""
        try:
            print(f"  [ReAct Supp] Fetching winds aloft...")
            result = get_winds_aloft_tool.invoke({
                "departure_icao": departure,
                "destination_icao": destination,
                "cruise_altitude_ft": 6000,
                "true_airspeed_kts": float(state["true_airspeed_kts"]),
            })
            print(f"  [ReAct Supp] Winds aloft fetched")
            return "winds_aloft", result
        except Exception as e:
            print(f"  [ReAct Supp] Winds aloft ERROR: {e}\n{traceback.format_exc()}")
            return "winds_aloft", f"Winds aloft unavailable: {e}"

    print(f"  [ReAct Supp] Running supplementary checks in parallel...")
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(_run_pireps),
            pool.submit(_run_sigmets),
            pool.submit(_run_crosswind),
            pool.submit(_run_route_weather),
            pool.submit(_run_winds_aloft),
        ]
        supp = dict(f.result() for f in futures)

    print(f"  [ReAct Supp] Supplementary checks complete")
    return {k: v for k, v in supp.items() if v}


def _extract_state(messages: list) -> dict:
    """
    Walk the ReAct message history and map tool outputs
    back to BriefingState fields.
    """
    updates = {
        "departure_metar":         None,
        "departure_taf":           None,
        "departure_notams":        None,
        "destination_metar":       None,
        "destination_taf":         None,
        "destination_notams":      None,
        "risk_assessment":         None,
        "fuel_analysis":           None,
        "alternates":              None,
        "night_currency_check":    None,
        "crosswind_analysis":      None,
        "pireps":                  None,
        "sigmets":                 None,
        "winds_aloft":             None,
        "route_weather":           None,
        "destination_is_unusable": False,
        "reason_unusable":         None,
    }

    for msg in messages:
        if not hasattr(msg, "name") or not msg.name:
            continue

        content = msg.content or ""

        if msg.name == "score_flight_risk":
            updates["risk_assessment"] = content
            if "NO-GO" in content or "EXTREME" in content:
                updates["destination_is_unusable"] = True
                updates["reason_unusable"] = _extract_reason(content)

        elif msg.name == "calculate_fuel":
            updates["fuel_analysis"] = content
            if "INSUFFICIENT" in content:
                updates["destination_is_unusable"] = True
                updates["reason_unusable"] = "Insufficient fuel"

        elif msg.name == "suggest_alternates":
            updates["alternates"] = content

        elif msg.name == "check_night_currency":
            updates["night_currency_check"] = content

    return updates


def _extract_reason(risk_text: str) -> str:
    for line in risk_text.splitlines():
        if "CRITICAL" in line or "WARNING" in line:
            return line.strip().lstrip("- ")
    return "High risk conditions at destination"
