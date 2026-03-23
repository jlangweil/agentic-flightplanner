import asyncio
from langchain.tools import tool
from pydantic import BaseModel, Field
from app.models import PirepData

# Severity ranks for filtering — 0=none, 1=light, 2=lgt-mod, 3=moderate, 4=mod-sev, 5=severe+
_TURB_RANK = {
    "NEG": 0, "SMTH": 0, "SMTH-LGT": 1,
    "LGT": 1, "LGT-MOD": 2, "MOD": 3,
    "MOD-SEV": 4, "SEV": 5, "EXTRM": 5,
}
_ICING_RANK = {
    "NEG": 0, "TRC": 1, "TRC-LGT": 1,
    "LGT": 1, "LGT-MOD": 2, "MOD": 3,
    "MOD-SEV": 4, "SEV": 5,
}


class PirepInput(BaseModel):
    icao: str = Field(description="ICAO code of the airport to search around")
    radius_nm: int = Field(
        default=100,
        description="Search radius in nautical miles (default 100nm)",
    )


def _rank_pirep(p: PirepData) -> int:
    """Return the maximum severity rank for a PIREP (higher = more significant)."""
    tb = _TURB_RANK.get(p.turbulence_intensity or "", 0)
    ic = _ICING_RANK.get(p.icing_intensity or "", 0)
    return max(tb, ic)


def _format_pirep_line(p: PirepData) -> str:
    parts = [f"  [{p.icao}]"]
    if p.altitude_ft:
        parts.append(f"FL{p.altitude_ft // 100:03d}")
    if p.aircraft_type:
        parts.append(f"/{p.aircraft_type}")
    if p.turbulence_intensity:
        tb_str = f"TB:{p.turbulence_intensity}"
        if p.turbulence_base_ft:
            top = p.turbulence_top_ft or "?"
            tb_str += f" {p.turbulence_base_ft // 100}–{top // 100 if isinstance(top, int) else top}k"
        parts.append(tb_str)
    if p.icing_intensity:
        ic_str = f"IC:{p.icing_intensity}"
        if p.icing_type:
            ic_str += f"-{p.icing_type}"
        if p.icing_base_ft:
            top = p.icing_top_ft or "?"
            ic_str += f" {p.icing_base_ft // 100}–{top // 100 if isinstance(top, int) else top}k"
        parts.append(ic_str)
    if p.wx_string:
        parts.append(f"WX:{p.wx_string}")
    return " ".join(parts)


@tool("get_pireps", args_schema=PirepInput)
def get_pireps_tool(icao: str, radius_nm: int = 100) -> str:
    """
    Fetch recent Pilot Reports (PIREPs) within a given radius of an airport.
    PIREPs report actual in-flight conditions including turbulence, icing, and
    visibility. Call for both the departure and destination airports to assess
    enroute conditions. Reports cover the last 3 hours.
    """
    from app.fetchers import get_pireps as _fetch

    try:
        pireps = asyncio.run(_fetch(icao, radius_nm))
    except Exception as e:
        return f"PIREPs — {icao} ({radius_nm}nm): unavailable ({e})"

    if not pireps:
        return f"PIREPs — {icao} ({radius_nm}nm): No recent reports"

    significant = [p for p in pireps if _rank_pirep(p) >= 3]
    routine = [p for p in pireps if _rank_pirep(p) < 3]

    lines = [
        f"PIREPs — {icao} ({radius_nm}nm radius, last 3h): {len(pireps)} total"
    ]

    if significant:
        lines.append(f"  SIGNIFICANT ({len(significant)}):")
        for p in significant[:6]:
            lines.append(_format_pirep_line(p))
        if len(significant) > 6:
            lines.append(f"  ... and {len(significant) - 6} more significant reports")
    else:
        lines.append("  No significant turbulence or icing reports")

    if routine:
        lines.append(f"  Routine ({len(routine)}):")
        for p in routine[:3]:
            lines.append(_format_pirep_line(p))
        if len(routine) > 3:
            lines.append(f"  ... and {len(routine) - 3} more")

    # Top-level flags
    has_severe_turb = any(_TURB_RANK.get(p.turbulence_intensity or "", 0) >= 4 for p in pireps)
    has_mod_ice = any(_ICING_RANK.get(p.icing_intensity or "", 0) >= 3 for p in pireps)
    if has_severe_turb:
        lines.append("  WARNING: Severe or extreme turbulence reported in area")
    if has_mod_ice:
        lines.append("  WARNING: Moderate or greater icing reported in area")

    return "\n".join(lines)
