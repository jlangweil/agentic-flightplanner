import streamlit as st
import threading
import queue
import uuid
import json
import re
from app.startup import initialize
from app.agent import dispatcher
from app.state import initial_state
from langgraph.types import Command

# ── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Smart Dispatcher",
    page_icon="✈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

initialize()

# ── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1200px; }

  /* Header */
  .sd-title {
    font-size: 2.1rem; font-weight: 900; letter-spacing: -1px;
    background: linear-gradient(90deg, #60a5fa 0%, #a78bfa 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    line-height: 1.1;
  }
  .sd-subtitle { color: #64748b; font-size: 0.82rem; margin-top: 2px; }

  /* Route pill */
  .route-pill {
    display: inline-flex; align-items: center; gap: 6px;
    background: #1e293b; border: 1px solid #334155; border-radius: 20px;
    padding: 5px 14px; font-family: monospace; font-size: 1rem; font-weight: 700;
    color: #e2e8f0;
  }

  /* Flight category badges */
  .cat-badge { display: inline-block; padding: 2px 9px; border-radius: 4px;
    font-size: 0.75rem; font-weight: 800; font-family: monospace; letter-spacing: 1px; }
  .cat-VFR  { background: #16a34a; color: #fff; }
  .cat-MVFR { background: #2563eb; color: #fff; }
  .cat-IFR  { background: #dc2626; color: #fff; }
  .cat-LIFR { background: #db2777; color: #fff; }

  /* Verdict banners */
  .verdict-GO {
    background: linear-gradient(135deg, #14532d 0%, #166534 100%);
    border: 1px solid #4ade80; border-radius: 14px;
    padding: 22px 28px; margin: 8px 0; text-align: center;
  }
  .verdict-NO-GO {
    background: linear-gradient(135deg, #450a0a 0%, #991b1b 100%);
    border: 1px solid #f87171; border-radius: 14px;
    padding: 22px 28px; margin: 8px 0; text-align: center;
  }
  .verdict-CAUTION, .verdict-MARGINAL {
    background: linear-gradient(135deg, #431407 0%, #92400e 100%);
    border: 1px solid #fbbf24; border-radius: 14px;
    padding: 22px 28px; margin: 8px 0; text-align: center;
  }
  .verdict-UNKNOWN {
    background: #1e293b; border: 1px solid #334155; border-radius: 14px;
    padding: 22px 28px; margin: 8px 0; text-align: center;
  }
  .verdict-label { font-size: 2rem; font-weight: 900; letter-spacing: 5px; color: #f1f5f9; }
  .verdict-sub   { font-size: 0.88rem; color: #cbd5e1; margin-top: 6px; }

  /* Metric cards */
  .metric-card {
    background: #1e293b; border: 1px solid #334155; border-radius: 12px;
    padding: 16px 18px; margin-bottom: 8px; height: 100%;
  }
  .metric-label {
    font-size: 0.68rem; font-weight: 700; letter-spacing: 1.2px;
    color: #64748b; text-transform: uppercase; margin-bottom: 6px;
  }
  .metric-value { font-size: 1.6rem; font-weight: 800; color: #e2e8f0; line-height: 1.1; }
  .metric-sub   { font-size: 0.78rem; color: #94a3b8; margin-top: 4px; }

  /* Status colours */
  .ok      { color: #4ade80 !important; }
  .caution { color: #fbbf24 !important; }
  .warning { color: #f87171 !important; }

  /* Risk bar */
  .risk-bar-track {
    background: #334155; border-radius: 6px; height: 8px; margin-top: 6px; overflow: hidden;
  }
  .risk-bar-fill {
    height: 100%; border-radius: 6px;
    transition: width 0.4s ease;
  }

  /* Step progress */
  .step-row { display: flex; align-items: center; gap: 10px; padding: 5px 0; font-size: 0.85rem; }
  .step-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .step-pending .step-dot { background: #334155; }
  .step-pending span      { color: #475569; }
  @keyframes spin {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
  }
  .step-active .step-dot {
    background: transparent;
    border: 2px solid #334155;
    border-top-color: #60a5fa;
    animation: spin 0.75s linear infinite;
  }
  .step-active span { color: #93c5fd; font-weight: 600; }
  .step-done    .step-dot { background: #4ade80; }
  .step-done    span      { color: #4ade80; }

  /* Detail section header */
  .detail-header {
    font-size: 0.7rem; font-weight: 700; letter-spacing: 1.5px;
    color: #60a5fa; text-transform: uppercase; margin-bottom: 4px;
  }

  /* Briefing card */
  .brief-section {
    border-left: 3px solid #3b82f6; padding: 12px 16px;
    margin: 10px 0; border-radius: 0 8px 8px 0; background: #0f172a;
  }
  .brief-title {
    font-size: 0.7rem; font-weight: 700; letter-spacing: 1.5px;
    color: #60a5fa; text-transform: uppercase; margin-bottom: 8px;
  }
  .brief-body { font-family: monospace; font-size: 0.82rem; color: #cbd5e1; white-space: pre-wrap; }

  /* Highlight borders for verdict-coloured sections */
  .brief-section-go     { border-left-color: #4ade80; }
  .brief-section-nogo   { border-left-color: #f87171; }
  .brief-section-warn   { border-left-color: #fbbf24; }

  /* ── Decision tiles — fixed height so all boxes match regardless of content ── */
  .tile {
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 8px;
    box-sizing: border-box;
    width: 100%;
    height: 118px;          /* FIXED: every tile is exactly this tall */
    overflow: hidden;       /* clip any overflow — never grows */
    position: relative;
  }
  .tile-nogo    { background: linear-gradient(135deg,#450a0a,#7f1d1d); border: 1px solid #f87171; }
  .tile-warning { background: linear-gradient(135deg,#431407,#78350f); border: 1px solid #fbbf24; }
  .tile-ok      { background: linear-gradient(135deg,#14532d,#166534); border: 1px solid #4ade80; }
  .tile-missing { background: #0f172a; border: 1px solid #334155; }
  .tile-info    { background: #1e293b; border: 1px solid #475569; }

  .tile-icon  { font-size: 1rem; line-height: 1; margin-bottom: 2px; }
  .tile-label {
    font-size: 0.58rem; font-weight: 700; letter-spacing: 1px;
    color: #94a3b8; text-transform: uppercase;
    margin-bottom: 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .tile-value {
    font-size: 1.05rem; font-weight: 800; color: #f1f5f9; line-height: 1.25;
    margin-bottom: 3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .tile-detail {
    font-size: 0.68rem; color: #94a3b8;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  .tile-badge {
    display: inline-block; font-size: 0.58rem; font-weight: 800;
    letter-spacing: 0.8px; padding: 2px 6px; border-radius: 3px;
    margin-right: 5px; vertical-align: middle;
  }
  .badge-nogo    { background: #f87171; color: #450a0a; }
  .badge-warning { background: #fbbf24; color: #431407; }
  .badge-ok      { background: #4ade80; color: #14532d; }
  .badge-missing { background: #475569; color: #cbd5e1; }

  /* ── Critic panel ─────────────────────────────────────────────────────── */
  .critic-panel {
    background: #1e293b; border: 1px solid #475569; border-radius: 12px;
    padding: 18px 22px; margin: 14px 0;
  }
  .critic-header {
    font-size: 0.68rem; font-weight: 700; letter-spacing: 1.5px;
    color: #60a5fa; text-transform: uppercase; margin-bottom: 10px;
  }
  .critic-verdict-agree    { color: #4ade80; font-weight: 800; font-size: 1.05rem; }
  .critic-verdict-caution  { color: #fbbf24; font-weight: 800; font-size: 1.05rem; }
  .critic-verdict-disagree { color: #f87171; font-weight: 800; font-size: 1.05rem; }
  .critic-summary { font-size: 0.88rem; color: #e2e8f0; margin: 8px 0 10px 0; }
  .critic-concern {
    font-size: 0.82rem; color: #cbd5e1; padding: 4px 0 4px 12px;
    border-left: 2px solid #475569; margin-bottom: 5px;
  }
  .critic-concern-warn { border-left-color: #fbbf24; }
  .critic-concern-nogo { border-left-color: #f87171; color: #fca5a5; }

  /* Hide Streamlit chrome */
  #MainMenu, footer, header { visibility: hidden; }
  [data-testid="stToolbar"]  { display: none; }
</style>
""", unsafe_allow_html=True)

# ── Session state defaults ───────────────────────────────────────────────────
_DEFAULTS = {
    "thread_id":     None,
    "phase":         "input",
    "trace_lines":   [],
    "state_snap":    None,   # raw graph_state.values dict
    "briefing":      None,
    "pilot_decision": None,
    "query":         "",
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _cat_badge(metar_json: str | None) -> str:
    if not metar_json:
        return ""
    try:
        m = json.loads(metar_json)
        cat = m.get("flight_category", "")
        if cat:
            return f'<span class="cat-badge cat-{cat}">{cat}</span>'
    except Exception:
        pass
    return ""


def _metar_line(metar_json: str | None) -> str:
    if not metar_json:
        return "No data"
    try:
        m = json.loads(metar_json)
        parts = []
        vis = m.get("visibility_sm")
        ceil = m.get("ceiling_ft")
        ws = m.get("wind_speed_kts")
        wd = m.get("wind_dir")
        wx = m.get("weather")
        if vis is not None:
            parts.append(f"{vis} SM")
        if ceil is not None:
            parts.append(f"Ceil {ceil:,} ft")
        if ws is not None:
            dir_str = f"{wd:03d}°/" if isinstance(wd, int) else ""
            parts.append(f"Wind {dir_str}{ws} kts")
        if ws == 0:
            parts.append("Calm")
        if wx:
            parts.append(wx)
        return " · ".join(parts) if parts else "Conditions normal"
    except Exception:
        return "Parse error"


def _parse_risk(text: str | None) -> tuple[int | None, str, str]:
    """Returns (score 0-10, level, verdict)."""
    if not text:
        return None, "UNKNOWN", "UNKNOWN"
    score, level, verdict = None, "UNKNOWN", "UNKNOWN"
    for line in text.splitlines():
        if "Score:" in line:
            try:
                score = int(line.split("Score:")[1].strip().split("/")[0].strip())
            except (ValueError, IndexError):
                pass
        elif "Level:" in line:
            level = line.split("Level:")[1].strip()
        elif "Verdict:" in line:
            verdict = line.split("Verdict:")[1].strip()
    return score, level, verdict


def _risk_bar_color(score: int | None) -> str:
    if score is None:
        return "#64748b"
    if score >= 9:
        return "#7c3aed"
    if score >= 6:
        return "#dc2626"
    if score >= 3:
        return "#d97706"
    return "#16a34a"


def _extract_line_value(text: str | None, keyword: str) -> str:
    """Find first line containing keyword, return text after the colon."""
    if not text:
        return "N/A"
    for line in text.splitlines():
        if keyword in line and ":" in line:
            return line.split(":", 1)[1].strip()
    return "N/A"


def _has_flag(text: str | None, *keywords: str) -> bool:
    if not text:
        return False
    return any(kw in text for kw in keywords)


# ── Decision tile helpers ─────────────────────────────────────────────────────

def _tile(icon: str, label: str, status: str, value: str, detail: str = "",
          tooltip: str = "") -> str:
    """
    Render a single colored decision tile.
    status: "nogo" | "warning" | "ok" | "missing" | "info"
    tooltip: full raw text shown on hover (browser native title attribute)
    """
    badge_map = {
        "nogo":    ('<span class="tile-badge badge-nogo">NO-GO</span>', "tile-nogo"),
        "warning": ('<span class="tile-badge badge-warning">CAUTION</span>', "tile-warning"),
        "ok":      ('<span class="tile-badge badge-ok">OK</span>', "tile-ok"),
        "missing": ('<span class="tile-badge badge-missing">NO DATA</span>', "tile-missing"),
        "info":    ("", "tile-info"),
    }
    badge_html, css = badge_map.get(status, ("", "tile-missing"))
    detail_html = f'<div class="tile-detail">{detail}</div>' if detail else ""
    # Escape quotes/newlines in tooltip for safe HTML attribute embedding
    _tip = tooltip.replace('"', "'").replace("\n", " | ") if tooltip else ""
    title_attr = f' title="{_tip}"' if _tip else ""
    return (
        f'<div class="tile {css}"{title_attr}>'
        f'<div class="tile-icon">{icon}</div>'
        f'<div class="tile-label">{label}</div>'
        f'<div class="tile-value">{badge_html}{value}</div>'
        f'{detail_html}'
        f'</div>'
    )


def _wx_status(metar_json: str | None) -> str:
    """Map flight category to tile status."""
    if not metar_json:
        return "missing"
    try:
        cat = json.loads(metar_json).get("flight_category", "")
        return {"LIFR": "nogo", "IFR": "nogo", "MVFR": "warning", "VFR": "ok"}.get(cat, "missing")
    except Exception:
        return "missing"


def _build_tiles(snap: dict) -> list[tuple]:
    """Return list of (icon, label, status, value, detail, tooltip) for the dashboard."""
    tiles = []

    # 1. Risk assessment
    risk_text = snap.get("risk_assessment") or ""
    score, level, _ = _parse_risk(risk_text)
    if risk_text:
        if level in ("EXTREME", "HIGH") or (score is not None and score >= 7):
            rs = "nogo"
        elif level == "MODERATE" or (score is not None and score >= 4):
            rs = "warning"
        else:
            rs = "ok"
        # Tooltip: list each risk factor
        factors = [ln.strip().lstrip("- ") for ln in risk_text.splitlines()
                   if ln.strip().startswith("- ")]
        tip = "\n".join(factors) if factors else risk_text[:300]
        tiles.append(("⚠️", "Risk Score", rs,
                       f"{score}/10" if score is not None else level, level, tip))
    else:
        tiles.append(("⚠️", "Risk Score", "missing", "—", "Not scored", ""))

    # 2. Departure weather
    dep_metar = snap.get("departure_metar")
    dep_cat = ""
    try:
        dep_cat = json.loads(dep_metar or "{}").get("flight_category", "")
    except Exception:
        pass
    tiles.append(("🌤", f"Departure · {snap.get('departure_icao','')}", _wx_status(dep_metar),
                   dep_cat or "—", _metar_line(dep_metar),
                   snap.get("departure_taf") and _extract_taf_tip(snap.get("departure_taf")) or _metar_line(dep_metar)))

    # 3. Destination weather
    dest_metar = snap.get("destination_metar")
    dest_cat = ""
    try:
        dest_cat = json.loads(dest_metar or "{}").get("flight_category", "")
    except Exception:
        pass
    tiles.append(("🌤", f"Destination · {snap.get('destination_icao','')}", _wx_status(dest_metar),
                   dest_cat or "—", _metar_line(dest_metar),
                   snap.get("destination_taf") and _extract_taf_tip(snap.get("destination_taf")) or _metar_line(dest_metar)))

    # 4. Crosswind
    cw = snap.get("crosswind_analysis") or ""
    if cw:
        if _has_flag(cw, "WARNING", "EXCEEDS"):
            cw_status = "nogo"
        elif "CAUTION" in cw:
            cw_status = "warning"
        else:
            cw_status = "ok"
        cw_val = _extract_line_value(cw, "Best runway")
        if cw_val == "N/A" or not cw_val:
            for ln in cw.splitlines():
                if "VRB" in ln or "Variable" in ln.lower():
                    cw_val = ln.strip()
                    break
        if "—" in (cw_val or ""):
            cw_val = cw_val.split("—", 1)[1].strip()
        tiles.append(("💨", "Crosswind", cw_status, cw_val or "OK", "Best available runway", cw))
    else:
        tiles.append(("💨", "Crosswind", "missing", "—", "No wind or runway data", ""))

    # 5. En-route weather
    rw = snap.get("route_weather") or ""
    if rw:
        if _has_flag(rw, "WARNING: IFR", "WARNING: LIFR", "WARNING: Severe"):
            rw_status = "nogo"
        elif _has_flag(rw, "WARNING", "CAUTION", "NO_FORECAST"):
            rw_status = "warning"
        else:
            rw_status = "ok"
        rw_val = "—"
        for ln in rw.splitlines():
            if "Worst en-route category:" in ln:
                rw_val = ln.split(":")[-1].strip()
                break
        product = "METAR+TAF" if "METAR+TAF" in rw else ("MOS" if "MOS" in rw else ("No forecast" if "NO_FORECAST" in rw else ""))
        tiles.append(("🗺", "En-Route Weather", rw_status, rw_val, product, rw))
    else:
        tiles.append(("🗺", "En-Route Weather", "missing", "—", "Not checked", ""))

    # 6. Fuel
    fuel = snap.get("fuel_analysis") or ""
    if fuel:
        fuel_status = "nogo" if _has_flag(fuel, "INSUFFICIENT", "NO-GO") else (
                      "warning" if _has_flag(fuel, "CAUTION", "WARNING") else "ok")
        margin = _extract_line_value(fuel, "Margin")
        tiles.append(("⛽", "Fuel Margin", fuel_status, margin, "FAR 91 reserves", fuel))
    else:
        tiles.append(("⛽", "Fuel Margin", "missing", "—", "Aircraft params not provided", ""))

    # 7. PIREPs
    pireps = snap.get("pireps") or ""
    if pireps:
        if "WARNING" in pireps:
            p_status, p_val = "warning", "Hazardous reports"
        elif "SIGNIFICANT" in pireps and "No significant" not in pireps:
            p_status, p_val = "warning", "Significant reports"
        else:
            p_status, p_val = "ok", "No significant reports"
        tiles.append(("🧑‍✈️", "PIREPs", p_status, p_val, "Pilot reports along corridor", pireps))
    else:
        tiles.append(("🧑‍✈️", "PIREPs", "missing", "—", "No recent reports", ""))

    # 8. SIGMETs / AIRMETs
    sig = snap.get("sigmets") or ""
    if sig:
        if "WARNING" in sig:
            sig_status, sig_val = "nogo", "Active SIGMET"
        elif "CAUTION" in sig or ("SIGMET" in sig and "No active" not in sig):
            sig_status, sig_val = "warning", "Advisory active"
        else:
            sig_status, sig_val = "ok", "No advisories"
        tiles.append(("🚨", "SIGMETs / AIRMETs", sig_status, sig_val, "En-route airspace", sig))
    else:
        tiles.append(("🚨", "SIGMETs / AIRMETs", "missing", "—", "Not checked", ""))

    # 9. Winds aloft (informational)
    wa = snap.get("winds_aloft") or ""
    if wa:
        gs = _extract_line_value(wa, "Ground speed")
        hw = _extract_line_value(wa, "component")
        tiles.append(("🌬️", "Winds Aloft", "info", gs or "Available", hw or "", wa))
    else:
        tiles.append(("🌬️", "Winds Aloft", "missing", "—", "TAS not provided or unavailable", ""))

    # 10. Night currency
    night = snap.get("night_currency_check") or ""
    if night:
        if _has_flag(night, "WARNING", "NO-GO"):
            n_status, n_val = "nogo", "Not current"
        elif "CAUTION" in night:
            n_status, n_val = "warning", "Review required"
        elif "outside FAR 61.57" in night:
            n_status, n_val = "ok", "Not applicable"
        else:
            n_status, n_val = "ok", "Current"
        tiles.append(("🌙", "Night Currency", n_status, n_val, "FAR 61.57(b)", night))

    return tiles


def _extract_taf_tip(taf_json: str | None) -> str:
    """Build a short tooltip from TAF JSON showing key forecast periods."""
    if not taf_json:
        return ""
    try:
        taf = json.loads(taf_json)
        lines = []
        for p in (taf.get("forecast_periods") or [])[:5]:
            parts = []
            if p.get("time_from"):
                parts.append(str(p["time_from"])[:16])
            if p.get("ceiling_ft") and p.get("ceiling_coverage") in ("BKN", "OVC"):
                parts.append(f"Ceil {p['ceiling_ft']}ft {p['ceiling_coverage']}")
            if p.get("visibility_sm") is not None:
                parts.append(f"Vis {p['visibility_sm']}sm")
            if p.get("weather"):
                parts.append(p["weather"])
            if p.get("change_type"):
                parts.append(f"({p['change_type']})")
            if parts:
                lines.append(" | ".join(parts))
        return "\n".join(lines)
    except Exception:
        return ""


def _render_tiles(tiles: list):
    """Render decision tiles in a fixed 5-column grid."""
    for i in range(0, len(tiles), 5):
        row = tiles[i:i + 5]
        cols = st.columns(5)
        for j, col in enumerate(cols):
            if j < len(row):
                icon, label, status, value, detail, tooltip = row[j]
                col.markdown(_tile(icon, label, status, value, detail, tooltip), unsafe_allow_html=True)
            else:
                col.markdown("", unsafe_allow_html=True)


def _render_critic(critic_text: str | None):
    """Render the critic review panel always open."""
    if not critic_text:
        return
    verdict, summary, concerns = "", "", []
    in_concerns = False
    for line in critic_text.splitlines():
        line = line.strip()
        if line.startswith("VERDICT:"):
            verdict = line.split(":", 1)[1].strip()
        elif line.startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()
        elif line.startswith("CONCERNS:"):
            in_concerns = True
        elif in_concerns and line.startswith("-"):
            concerns.append(line[1:].strip())

    # Map internal AGREE/CAUTION/DISAGREE to plain-English display labels
    verdict_display = {
        "AGREE":    "✅ Confirms assessment",
        "CAUTION":  "⚠️ Caution flagged",
        "DISAGREE": "⛔ Overrides to NO-GO",
    }.get(verdict, verdict)

    verdict_cls = {
        "AGREE":    "critic-verdict-agree",
        "CAUTION":  "critic-verdict-caution",
        "DISAGREE": "critic-verdict-disagree",
    }.get(verdict, "critic-verdict-caution")

    is_nogo = verdict == "DISAGREE"
    concerns_html = "".join(
        f'<div class="critic-concern {"critic-concern-nogo" if is_nogo else "critic-concern-warn"}">'
        f'{"⛔" if is_nogo else "⚠"} {c}</div>'
        for c in concerns
        if c and c.lower() not in ("none", "")
    )
    st.markdown(
        f'<div class="critic-panel">'
        f'<div class="critic-header">🔍 CFII Critic Review</div>'
        f'<div><span class="{verdict_cls}">{verdict_display}</span></div>'
        f'<div class="critic-summary">{summary}</div>'
        f'{concerns_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _query_banner(query: str):
    """Show the user's flight query prominently."""
    if not query:
        return
    st.markdown(
        f'<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;'
        f'padding:12px 18px;margin-bottom:12px;">'
        f'<div style="font-size:0.65rem;font-weight:700;letter-spacing:1.2px;color:#64748b;'
        f'text-transform:uppercase;margin-bottom:6px;">Your Request</div>'
        f'<div style="font-size:0.92rem;color:#e2e8f0;">{query}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_route_map(snap: dict):
    """Render a Plotly geo scatter map with origin, destination, and corridor waypoints."""
    import plotly.graph_objects as go
    from app.airport_db import get_airport, find_corridor_airports

    dep_icao  = snap.get("departure_icao", "")
    dest_icao = snap.get("destination_icao", "")
    if not dep_icao or not dest_icao:
        return

    dep  = get_airport(dep_icao)
    dest = get_airport(dest_icao)
    if not dep or not dest:
        return

    # Great-circle line (20 pts)
    N = 20
    route_lats = [dep["lat"] + (i / N) * (dest["lat"] - dep["lat"]) for i in range(N + 1)]
    route_lons = [dep["lon"] + (i / N) * (dest["lon"] - dep["lon"]) for i in range(N + 1)]

    # Corridor waypoints
    corridor = find_corridor_airports(dep_icao, dest_icao, corridor_nm=25.0)

    fig = go.Figure()

    # Route line
    fig.add_trace(go.Scattergeo(
        lat=route_lats, lon=route_lons,
        mode="lines",
        line=dict(color="#60a5fa", width=2),
        name="Route",
        hoverinfo="skip",
    ))

    # Corridor waypoints
    if corridor:
        wp_lats = [a["lat"] for a in corridor]
        wp_lons = [a["lon"] for a in corridor]
        wp_text = [f'{a["icao"]}<br>{a["name"][:30]}' for a in corridor]
        fig.add_trace(go.Scattergeo(
            lat=wp_lats, lon=wp_lons,
            mode="markers",
            marker=dict(size=7, color="#fbbf24", symbol="circle"),
            text=wp_text, hoverinfo="text",
            name="En-route",
        ))

    # Departure marker
    fig.add_trace(go.Scattergeo(
        lat=[dep["lat"]], lon=[dep["lon"]],
        mode="markers+text",
        marker=dict(size=11, color="#4ade80", symbol="circle"),
        text=[dep_icao], textposition="top right",
        textfont=dict(size=11, color="#4ade80"),
        hovertext=f'{dep_icao} — {dep["name"]}',
        hoverinfo="text", name="Departure",
    ))

    # Destination marker
    fig.add_trace(go.Scattergeo(
        lat=[dest["lat"]], lon=[dest["lon"]],
        mode="markers+text",
        marker=dict(size=11, color="#f87171", symbol="circle"),
        text=[dest_icao], textposition="top right",
        textfont=dict(size=11, color="#f87171"),
        hovertext=f'{dest_icao} — {dest["name"]}',
        hoverinfo="text", name="Destination",
    ))

    # Map bounds with padding
    all_lats = route_lats + [a["lat"] for a in corridor]
    all_lons = route_lons + [a["lon"] for a in corridor]
    lat_pad = max(1.5, (max(all_lats) - min(all_lats)) * 0.4)
    lon_pad = max(2.0, (max(all_lons) - min(all_lons)) * 0.4)

    fig.update_layout(
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        margin=dict(l=0, r=0, t=0, b=0),
        height=280,
        legend=dict(
            bgcolor="#1e293b", font=dict(color="#94a3b8", size=11),
            bordercolor="#334155", borderwidth=1,
            orientation="h", x=0, y=1.02,
        ),
        geo=dict(
            bgcolor="#0f172a",
            landcolor="#1e293b",
            oceancolor="#0f172a",
            lakecolor="#0f172a",
            coastlinecolor="#334155",
            countrycolor="#334155",
            showland=True, showocean=True, showlakes=True,
            showcoastlines=True, showcountries=True,
            lataxis=dict(range=[min(all_lats) - lat_pad, max(all_lats) + lat_pad]),
            lonaxis=dict(range=[min(all_lons) - lon_pad, max(all_lons) + lon_pad]),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _parse_briefing_sections(text: str) -> list[tuple[str, str]]:
    """Split briefing into [(title, body), ...] using ALL-CAPS headers with underlines."""
    sections: list[tuple[str, str]] = []
    pattern = re.compile(r"^([A-Z][A-Z /\-]+?)\s*\n[-=]+", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [("BRIEFING", text)]
    preamble = text[:matches[0].start()].strip()
    if preamble:
        sections.append(("PRE-FLIGHT BRIEFING", preamble))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append((title, body))
    return sections


_SECTION_ICON = {
    "PRE-FLIGHT BRIEFING": "📋",
    "WEATHER SUMMARY": "🌤",
    "RISK FACTORS": "⚠️",
    "CROSSWIND ANALYSIS": "💨",
    "WINDS ALOFT": "🌬️",
    "PIREPS": "🧑‍✈️",
    "SIGMETS / AIRMETS": "🚨",
    "FUEL ANALYSIS": "⛽",
    "NOTAMS": "📢",
    "ALTERNATES": "🛬",
    "CRITIC NOTES": "🔍",
    "RECOMMENDATION": "🏁",
}

_SECTION_VERDICT_CLASS = {
    "RECOMMENDATION": "brief-section-go",
    "RISK FACTORS": "brief-section-warn",
    "SIGMETS / AIRMETS": "brief-section-warn",
}

# Map log line prefixes/substrings to human-readable activity labels.
# The agent may do these in any order (especially in ReAct mode).
_ACTIVITY_PATTERNS: list[tuple[str, str]] = [
    ("[Planner]",              "✈  Route planned"),
    ("[ReAct Analyzer]",       "🤖  ReAct reasoning loop active"),
    ("[ReAct] -->",            "🔧  Calling weather tools..."),
    ("[ReAct Analyzer] Tools called", "🔧  ReAct tool calls complete"),
    ("[ReAct Supp] Fetching PIREPs",     "🧑‍✈️  Fetching PIREPs..."),
    ("[ReAct Supp] Fetching SIGMETs",    "🚨  Fetching SIGMETs/AIRMETs..."),
    ("[ReAct Supp] Calculating crosswind","💨  Calculating crosswind..."),
    ("[ReAct Supp] Fetching route",      "🗺  Fetching route weather..."),
    ("[ReAct Supp] Fetching winds aloft","🌬️  Fetching winds aloft..."),
    ("[ReAct Supp] Running supplementary", "⚙️  Supplementary checks starting..."),
    ("[ReAct Supp] Supplementary checks complete", "✅  All supplementary checks done"),
    ("[API]   METAR",          "🌤  METAR fetched (live)"),
    ("[CACHE] METAR",          "🌤  METAR loaded (cache)"),
    ("[API]   TAF",            "🌤  TAF fetched (live)"),
    ("[CACHE] TAF",            "🌤  TAF loaded (cache)"),
    ("[API]   PIREPs",         "🧑‍✈️  PIREPs fetched (live)"),
    ("[CACHE] PIREPs",         "🧑‍✈️  PIREPs loaded (cache)"),
    ("[API]   Winds aloft",    "🌬️  Winds aloft fetched (live)"),
    ("[CACHE] Winds aloft",    "🌬️  Winds aloft loaded (cache)"),
    ("[Winds]",                "🌬️  Winds aloft (nearest station)"),
    ("PIREPs fetched",         "🧑‍✈️  PIREPs combined for corridor"),
    ("SIGMETs/AIRMETs fetched","🚨  SIGMETs/AIRMETs checked"),
    ("[Analyzer] Crosswind",   "💨  Crosswind calculated"),
    ("[Analyzer] Winds aloft", "🌬️  Winds aloft analysed"),
    ("[Analyzer] Risk result", "⚠️  Risk scored"),
    ("[Analyzer] Fuel result", "⛽  Fuel calculated"),
    ("[Analyzer] Night",       "🌙  Night currency checked"),
    ("[ReAct Supp] PIREPs fetched",       "🧑‍✈️  PIREPs combined for corridor"),
    ("[ReAct Supp] SIGMETs",              "🚨  SIGMETs/AIRMETs checked"),
    ("[ReAct Supp] Crosswind",            "💨  Crosswind calculated"),
    ("[ReAct Supp] Route weather fetched","🗺  En-route weather checked"),
    ("[ReAct Supp] Winds aloft fetched",  "🌬️  Winds aloft fetched"),
    ("Route weather fetched",  "🗺  En-route weather checked"),
    ("Supplementary checks complete", "✅  All checks complete"),
    ("[FinalBriefing]",        "📋  Generating final briefing"),
    ("ERROR",                  "❌  Error occurred — see sidebar"),
    ("STDERR:",                "❌  Stderr output — see sidebar"),
]


def _activities_from_lines(lines: list[str]) -> list[str]:
    """
    Return activity labels in the order they were first seen in the log,
    without duplicates. Unknown lines are shown as-is (truncated).
    """
    seen_labels: set[str] = set()
    result: list[str] = []
    for line in lines:
        matched = False
        for pattern, label in _ACTIVITY_PATTERNS:
            if pattern in line and label not in seen_labels:
                seen_labels.add(label)
                result.append(label)
                matched = True
                break
        if not matched:
            # Surface unmatched lines that look like node transitions
            if line.startswith("[") and "]" in line and line not in seen_labels:
                seen_labels.add(line)
                truncated = line[:80] + ("…" if len(line) > 80 else "")
                result.append(f"   {truncated}")
    return result


def _render_activity_feed(activities: list[str], running: bool):
    html_parts = []
    for label in activities:
        is_error = label.startswith("❌")
        cls = "step-warning" if is_error else "step-done"
        html_parts.append(
            f'<div class="step-row {cls}"><div class="step-dot"></div><span>{label}</span></div>'
        )
    if running:
        html_parts.append(
            '<div class="step-row step-active">'
            '<div class="step-dot"></div><span>Working…</span></div>'
        )
    if html_parts:
        st.markdown("\n".join(html_parts), unsafe_allow_html=True)
    else:
        st.markdown('<div class="step-row step-active"><div class="step-dot"></div><span>Starting…</span></div>', unsafe_allow_html=True)


# ── Sidebar debug ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Debug")
    st.write(f"**Phase:** `{st.session_state.phase}`")
    st.write(f"**Thread:** `{str(st.session_state.thread_id)[:8] if st.session_state.thread_id else 'None'}`")

    # Show any errors captured in the trace log
    errors = [l for l in (st.session_state.trace_lines or []) if "ERROR" in l or "Traceback" in l or "Exception" in l]
    if errors:
        st.markdown("**Errors:**")
        st.code("\n".join(errors), language=None)

    # Show graph state snapshot
    snap = st.session_state.get("state_snap")
    if snap:
        st.markdown("**Graph state keys:**")
        # Show non-None values with their length/preview
        for k, v in snap.items():
            if v is not None and k != "messages":
                preview = str(v)[:60].replace("\n", " ") if v else ""
                st.write(f"`{k}`: {preview}")

        # Next nodes (stored separately via graph_state.next — not in snap)
        # We'll show it in a dedicated key if available
        if "_next_nodes" in st.session_state:
            st.write(f"**Next nodes:** `{st.session_state._next_nodes}`")
    else:
        st.write("No state snapshot yet")

    st.divider()
    if st.button("Reset session"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ── Page header ──────────────────────────────────────────────────────────────
col_title, col_route = st.columns([3, 2])
with col_title:
    st.markdown(
        '<div class="sd-title">✈ Smart Dispatcher</div>'
        '<div class="sd-subtitle">Agentic pre-flight briefing · Powered by Claude</div>',
        unsafe_allow_html=True,
    )
with col_route:
    snap = st.session_state.state_snap
    if snap:
        dep  = snap.get("departure_icao", "")
        dest = snap.get("destination_icao", "")
        if dep and dest:
            dep_badge  = _cat_badge(snap.get("departure_metar"))
            dest_badge = _cat_badge(snap.get("destination_metar"))
            st.markdown(
                f'<div style="text-align:right; padding-top:10px;">'
                f'<div class="route-pill">{dep} {dep_badge} → {dest} {dest_badge}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

st.divider()


# ════════════════════════════════════════════════════════════════════════════
#  INPUT
# ════════════════════════════════════════════════════════════════════════════
if st.session_state.phase == "input":

    query = st.text_area(
        "Describe your planned flight",
        value=st.session_state.query,
        placeholder=(
            "Should I fly from Morristown (KMMU) to Block Island (KBID) today? "
            "My Cessna burns 10 GPH, cruises at 120 knots, "
            "and I have 40 gallons on board."
        ),
        height=110,
        key="query_input",
        label_visibility="visible",
    )

    st.markdown("#### Pilot Qualifications")
    pq1, pq2, pq3, pq4 = st.columns(4)
    is_ifr       = pq1.checkbox("IFR flight",
                                 help="Check if filing an IFR flight plan")
    ifr_current  = pq2.checkbox("IFR current",
                                 help="Have you completed 6 instrument approaches + holds in the past 6 months? (FAR 61.57)")
    night_current = pq3.checkbox("Night current",
                                  help="Have you done 3 night takeoffs/landings in the past 90 days? (FAR 61.57)")
    carrying_pax = pq4.checkbox("Carrying passengers",
                                 help="Enables night and IFR currency checks per FAR 61.57")

    pm1, pm2, _ = st.columns([1, 1, 2])
    personal_min_ceiling = pm1.number_input(
        "Personal min ceiling (ft)",
        value=1000, step=100, min_value=0,
        help="Your personal minimums ceiling — a warning is added if forecast ceiling is lower",
    )
    personal_min_vis = pm2.number_input(
        "Personal min vis (SM)",
        value=5.0, step=0.5, min_value=0.0,
        help="Your personal minimums visibility — a warning is added if forecast vis is lower",
    )

    st.markdown("#### Aircraft Parameters")
    c1, c2, c3, c4 = st.columns(4)
    fuel_onboard = c1.number_input("Fuel (gal)",     value=40.0, step=1.0,  min_value=0.0)
    fuel_burn    = c2.number_input("Burn (GPH)",     value=10.0, step=0.5,  min_value=0.1)
    airspeed     = c3.number_input("TAS (kts)",      value=120,  step=5,    min_value=1)
    max_xwind    = c4.number_input("Max X-wind (kts)",value=15.0, step=1.0, min_value=0.0,
                                   help="Your aircraft's demonstrated crosswind component from the POH")

    st.markdown("")
    if st.button("▶  Run Pre-Flight Analysis", type="primary", use_container_width=True):
        raw = st.session_state.get("query_input", "").strip() or st.session_state.query.strip()
        if not raw:
            st.error("Please enter a flight query.")
        else:
            enriched = raw
            if str(int(fuel_onboard)) not in raw:
                enriched += (
                    f" I have {fuel_onboard} gallons on board, "
                    f"burn rate {fuel_burn} GPH, cruise {airspeed} knots."
                )
            if is_ifr and "IFR" not in raw.upper():
                enriched += " This is an IFR flight."
            if is_ifr:
                enriched += f" I {'am' if ifr_current else 'am NOT'} IFR current."
            if night_current:
                enriched += " I am night current."
            else:
                enriched += " I am not night current."
            if carrying_pax and "passenger" not in raw.lower():
                enriched += " I will be carrying passengers."
            if personal_min_ceiling is not None:
                enriched += f" My personal minimums are {int(personal_min_ceiling)}ft ceiling"
                if personal_min_vis is not None:
                    enriched += f" and {personal_min_vis}SM visibility."
                else:
                    enriched += "."
            elif personal_min_vis is not None:
                enriched += f" My personal minimum visibility is {personal_min_vis}SM."
            # Append max crosswind context
            enriched += f" My aircraft's demonstrated crosswind limit is {max_xwind} knots."

            st.session_state.query          = enriched
            st.session_state.thread_id      = str(uuid.uuid4())
            st.session_state.phase          = "running"
            st.session_state.trace_lines    = []
            st.session_state.briefing       = None
            st.session_state.state_snap     = None
            st.session_state.pilot_decision = None
            st.rerun()


# ════════════════════════════════════════════════════════════════════════════
#  RUNNING
# ════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "running":

    _query_banner(st.session_state.query)

    col_steps, col_log = st.columns([1, 2])

    with col_steps:
        st.markdown("#### Analysis Progress")
        steps_placeholder = st.empty()

    with col_log:
        st.markdown("#### Agent Log")
        log_placeholder = st.empty()

    output_queue: queue.Queue = queue.Queue()
    thread_id = st.session_state.thread_id
    query     = st.session_state.query

    def _run_agent(tid: str, q_text: str):
        import sys
        import io

        class _QWriter(io.TextIOBase):
            def __init__(self, prefix=""):
                self.prefix = prefix
                self._buf = ""
            def write(self, text):
                # Buffer until newline so partial writes don't flood the queue
                self._buf += text
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        output_queue.put(f"{self.prefix}{line}")
                return len(text)
            def flush(self):
                if self._buf.strip():
                    output_queue.put(f"{self.prefix}{self._buf.strip()}")
                    self._buf = ""

        old_out, old_err = sys.stdout, sys.stderr

        class _ErrWriter(io.TextIOBase):
            """Tees stderr to both the UI queue and the original terminal."""
            def __init__(self):
                self._buf = ""
            def write(self, text):
                old_err.write(text)   # Always echo to terminal
                old_err.flush()
                self._buf += text
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        output_queue.put(f"STDERR: {line}")
                return len(text)
            def flush(self):
                old_err.flush()
                if self._buf.strip():
                    output_queue.put(f"STDERR: {self._buf.strip()}")
                    self._buf = ""

        sys.stdout = _QWriter()
        sys.stderr = _ErrWriter()
        try:
            config = {"configurable": {"thread_id": tid}}
            state  = initial_state(q_text)
            dispatcher.invoke(state, config=config)
        except Exception as e:
            import traceback
            output_queue.put(f"ERROR: {e}")
            output_queue.put(traceback.format_exc())
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
            output_queue.put("__DONE__")

    thread = threading.Thread(target=_run_agent, args=(thread_id, query), daemon=True)
    thread.start()

    lines: list[str] = []
    _last_render = 0.0
    while True:
        try:
            line = output_queue.get(timeout=0.3)
            if line == "__DONE__":
                break
            lines.append(line)
            with steps_placeholder.container():
                _render_activity_feed(_activities_from_lines(lines), running=True)
            log_placeholder.code("\n".join(lines[-40:]), language=None)
            import time; _last_render = time.monotonic()
        except queue.Empty:
            if not thread.is_alive():
                break
            # Re-render every ~0.5 s even with no new output so the
            # "Working…" dot stays visible during silent LLM reasoning gaps.
            import time
            if time.monotonic() - _last_render >= 0.5:
                with steps_placeholder.container():
                    _render_activity_feed(_activities_from_lines(lines), running=True)
                _last_render = time.monotonic()

    # Final render — show completed state, no "Working..." spinner
    with steps_placeholder.container():
        _render_activity_feed(_activities_from_lines(lines), running=False)

    st.session_state.trace_lines = lines

    config      = {"configurable": {"thread_id": thread_id}}
    graph_state = dispatcher.get_state(config)
    snap        = dict(graph_state.values)
    st.session_state.state_snap  = snap
    st.session_state._next_nodes = list(graph_state.next)

    if "human_checkpoint" in graph_state.next:
        st.session_state.phase = "awaiting"
    else:
        st.session_state.briefing = snap.get("briefing")
        st.session_state.phase    = "done"

    st.rerun()


# ════════════════════════════════════════════════════════════════════════════
#  AWAITING PILOT CONFIRMATION
# ════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "awaiting":

    snap = st.session_state.state_snap or {}

    _query_banner(st.session_state.query)

    # ── Verdict banner ──────────────────────────────────────────────────────
    go_no_go = snap.get("go_no_go", "UNKNOWN") or "UNKNOWN"
    score, level, verdict_text = _parse_risk(snap.get("risk_assessment"))

    verdict_css = {
        "GO": "verdict-GO",
        "NO-GO": "verdict-NO-GO",
        "MARGINAL": "verdict-MARGINAL",
        "CAUTION — review carefully": "verdict-CAUTION",
    }.get(go_no_go, "verdict-UNKNOWN")

    st.markdown(
        f'<div class="{verdict_css}">'
        f'<div class="verdict-label">{go_no_go}</div>'
        f'<div class="verdict-sub">Risk level: {level}'
        + (f" · Score {score}/10+" if score is not None else "")
        + f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Decision tiles ───────────────────────────────────────────────────────
    _render_tiles(_build_tiles(snap))

    st.markdown("")

    # ── Route map ────────────────────────────────────────────────────────────
    _render_route_map(snap)

    # ── Risk score bar ───────────────────────────────────────────────────────
    if score is not None:
        pct = min(score / 10, 1.0) * 100
        bar_color = _risk_bar_color(score)
        st.markdown(
            f'<div class="metric-label">Risk Score: {score}/10+ · {level}</div>'
            f'<div class="risk-bar-track">'
            f'<div class="risk-bar-fill" style="width:{pct}%; background:{bar_color};"></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown("")

    # ── Critic review — always visible ───────────────────────────────────────
    _render_critic(snap.get("critic_feedback"))

    # ── Expandable raw data sections ─────────────────────────────────────────
    with st.expander("📊  Detailed Analysis Data", expanded=True):
        detail_sections = [
            ("⚠️  Risk Factors",        snap.get("risk_assessment")),
            ("💨  Crosswind Analysis",  snap.get("crosswind_analysis")),
            ("🌬️  Winds Aloft",         snap.get("winds_aloft")),
            ("🗺  En-Route Weather",    snap.get("route_weather")),
            ("🧑‍✈️  PIREPs",              snap.get("pireps")),
            ("🚨  SIGMETs / AIRMETs",   snap.get("sigmets")),
            ("⛽  Fuel Analysis",        snap.get("fuel_analysis")),
            ("📢  NOTAMs — Departure",   snap.get("departure_notams")),
            ("📢  NOTAMs — Destination", snap.get("destination_notams")),
            ("🛬  Alternates",           snap.get("alternates")),
            ("🌙  Night Currency",       snap.get("night_currency_check")),
        ]
        for title, content in detail_sections:
            if content and content.strip():
                st.markdown(f"**{title}**")
                st.code(content, language=None)

    with st.expander("📜  Agent Reasoning Log", expanded=False):
        st.code("\n".join(st.session_state.trace_lines or []), language=None)

    # ── Decision buttons ────────────────────────────────────────────────────
    st.divider()
    st.markdown("#### Your Decision")
    st.caption(
        "Review the assessment above. Click **GO** to generate the full briefing, "
        "or **NO-GO** to abort the flight."
    )

    btn_col1, btn_col2, _ = st.columns([1, 1, 3])
    if btn_col1.button("✅  GO", type="primary", use_container_width=True):
        st.session_state.pilot_decision = "GO"
        st.session_state.phase          = "resuming"
        st.rerun()
    if btn_col2.button("❌  NO-GO", use_container_width=True):
        st.session_state.pilot_decision = "NO-GO"
        st.session_state.phase          = "resuming"
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════
#  RESUMING
# ════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "resuming":

    decision  = st.session_state.pilot_decision
    thread_id = st.session_state.thread_id

    with st.spinner(f"Pilot decision: **{decision}** · Generating final briefing..."):
        config = {"configurable": {"thread_id": thread_id}}
        try:
            dispatcher.invoke(Command(resume=decision), config=config)
            final_state = dispatcher.get_state(config)
            snap = dict(final_state.values)
            st.session_state.state_snap = snap
            briefing = snap.get("briefing")

            if not briefing:
                v = snap
                briefing = (
                    f"PRE-FLIGHT BRIEFING\n{'='*50}\n"
                    f"Route:   {v.get('departure_icao')} → {v.get('destination_icao')}\n"
                    f"Verdict: {v.get('go_no_go', decision)}\n{'='*50}\n\n"
                    + "\n\n".join(filter(None, [
                        v.get("risk_assessment", ""),
                        v.get("crosswind_analysis", ""),
                        v.get("winds_aloft", ""),
                        v.get("pireps", ""),
                        v.get("fuel_analysis", ""),
                        v.get("critic_feedback", ""),
                    ]))
                )

            st.session_state.briefing = briefing
            st.session_state.phase    = "done"

        except Exception as e:
            import traceback
            st.error(f"Error: {e}")
            st.code(traceback.format_exc())
            st.session_state.phase = "input"

    st.rerun()


# ════════════════════════════════════════════════════════════════════════════
#  DONE
# ════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == "done":

    snap     = st.session_state.state_snap or {}
    decision = st.session_state.get("pilot_decision", "")
    briefing = st.session_state.get("briefing") or ""

    _query_banner(st.session_state.query)

    # ── Verdict banner ──────────────────────────────────────────────────────
    final_verdict = snap.get("go_no_go", decision) or decision
    verdict_css   = {"GO": "verdict-GO", "NO-GO": "verdict-NO-GO"}.get(final_verdict, "verdict-CAUTION")
    verdict_msg   = {
        "GO":    "Cleared for departure — full briefing below",
        "NO-GO": "Flight aborted — review conditions before rescheduling",
    }.get(final_verdict, "Review complete")

    st.markdown(
        f'<div class="{verdict_css}">'
        f'<div class="verdict-label">{final_verdict}</div>'
        f'<div class="verdict-sub">{verdict_msg}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Decision tiles dashboard ─────────────────────────────────────────────
    _render_tiles(_build_tiles(snap))

    st.markdown("")

    # ── Route map ────────────────────────────────────────────────────────────
    _render_route_map(snap)

    # Risk bar
    score, level, _ = _parse_risk(snap.get("risk_assessment"))
    if score is not None:
        pct = min(score / 10, 1.0) * 100
        st.markdown(
            f'<div class="metric-label">Risk Score: {score}/10+ · {level}</div>'
            f'<div class="risk-bar-track">'
            f'<div class="risk-bar-fill" style="width:{pct}%; background:{_risk_bar_color(score)};"></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown("")

    # ── Critic review — always visible ───────────────────────────────────────
    _render_critic(snap.get("critic_feedback"))

    # ── Formatted briefing ───────────────────────────────────────────────────
    if briefing:
        st.markdown("#### Pre-Flight Briefing")
        sections = _parse_briefing_sections(briefing)

        for title, body in sections:
            icon        = _SECTION_ICON.get(title, "")
            extra_class = _SECTION_VERDICT_CLASS.get(title, "")
            if title == "RECOMMENDATION":
                extra_class = {"NO-GO": "brief-section-nogo", "GO": "brief-section-go"}.get(
                    final_verdict, "brief-section-warn"
                )
            elif _has_flag(body, "WARNING", "CRITICAL", "EXCEEDS"):
                extra_class = "brief-section-warn"

            st.markdown(
                f'<div class="brief-section {extra_class}">'
                f'<div class="brief-title">{icon}  {title}</div>'
                f'<div class="brief-body">{body}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("")
        dl_col, _ = st.columns([1, 3])
        with dl_col:
            st.download_button(
                label="⬇  Download Briefing (.txt)",
                data=briefing,
                file_name=f"briefing_{str(st.session_state.get('thread_id','x'))[:8]}.txt",
                mime="text/plain",
                use_container_width=True,
            )
    else:
        st.warning("No briefing text was generated.")

    with st.expander("📜  Raw briefing text", expanded=False):
        st.code(briefing, language=None)

    with st.expander("📜  Agent reasoning log", expanded=False):
        st.code("\n".join(st.session_state.trace_lines or []), language=None)

    st.divider()
    if st.button("✈  New Briefing", type="primary", use_container_width=False):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
