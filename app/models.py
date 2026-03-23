from pydantic import BaseModel, Field
from typing import Optional

class MetarData(BaseModel):
    icao: str
    raw: str
    visibility_sm: Optional[float] = None
    ceiling_ft: Optional[int] = None
    ceiling_coverage: Optional[str] = None   # BKN, OVC, etc.
    wind_dir: Optional[int | str] = None   # int normally, "VRB" or "270V340" when variable
    wind_speed_kts: Optional[int] = None
    wind_gust_kts: Optional[int] = None
    weather: Optional[str] = None            # RA, TS, FG, etc.
    temp_c: Optional[float] = None
    altimeter: Optional[float] = None
    flight_category: Optional[str] = None    # VFR, MVFR, IFR, LIFR
    observed_time: Optional[int | str] = None

    @property
    def is_ifr(self) -> bool:
        return self.flight_category in ("IFR", "LIFR")

    @property
    def is_vfr(self) -> bool:
        return self.flight_category == "VFR"


class TafPeriod(BaseModel):
    time_from: Optional[int | str] = None
    time_to: Optional[int | str] = None
    wind_dir: Optional[int | str] = None
    wind_speed_kts: Optional[int] = None
    wind_gust_kts: Optional[int] = None
    visibility_sm: Optional[float] = None
    ceiling_ft: Optional[int] = None
    ceiling_coverage: Optional[str] = None
    weather: Optional[str] = None
    change_type: Optional[str] = None


class TafData(BaseModel):
    icao: str
    raw: str
    issued_time: Optional[int | str] = None
    valid_from: Optional[int | str] = None
    valid_to: Optional[int | str] = None
    forecast_periods: list[TafPeriod] = Field(default_factory=list)

class NotamData(BaseModel):
    notam_id: str
    location: str
    effective_start: Optional[str] = None
    effective_end: Optional[str] = None
    raw_text: str
    translated_text: Optional[str] = None
    category: Optional[str] = None      # RWY, NAV, COM, AIRSPACE, etc.
    is_critical: bool = False           # Runway closure, ILS out, etc.


class PirepData(BaseModel):
    icao: str
    raw: str
    obs_time: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_ft: Optional[int] = None        # fltLvl * 100
    aircraft_type: Optional[str] = None
    turbulence_intensity: Optional[str] = None   # NEG, LGT, MOD, SEV, EXTRM
    turbulence_base_ft: Optional[int] = None
    turbulence_top_ft: Optional[int] = None
    icing_intensity: Optional[str] = None        # NEG, LGT, MOD, SEV
    icing_type: Optional[str] = None             # RIME, CLEAR, MIXED
    icing_base_ft: Optional[int] = None
    icing_top_ft: Optional[int] = None
    temp_c: Optional[float] = None
    wind_dir: Optional[int] = None
    wind_speed_kts: Optional[int] = None
    visibility_sm: Optional[float] = None
    wx_string: Optional[str] = None
    pirep_type: str = "PIREP"


class WindsAloftStation(BaseModel):
    """Winds aloft forecast for one altitude level at one station."""
    station_id: str
    valid_time: Optional[str] = None
    altitude_ft: int                     # pressure altitude (3000–45000 ft)
    wind_dir: Optional[int] = None       # degrees true; None = calm/light
    wind_speed_kts: Optional[int] = None
    temp_c: Optional[float] = None


class MosPeriod(BaseModel):
    """One forecast period from a GFS MOS JSON response."""
    ftime: str                          # forecast valid time string from API
    tmp: Optional[int] = None           # temperature (°F)
    dpt: Optional[int] = None           # dew point (°F)
    wdr: Optional[int] = None           # wind direction (degrees, 0 = calm)
    wsp: Optional[int] = None           # wind speed (knots)
    sky: Optional[int] = None           # sky cover code 0–8
    cld: Optional[str] = None           # CLR / SCT / BKN / OVC
    vis: Optional[int] = None           # visibility code 0–8
    cig: Optional[int] = None           # ceiling code 0–10


class MosData(BaseModel):
    """GFS MOS forecast for one station."""
    station_id: str
    model_time: Optional[str] = None    # model run time string
    periods: list[MosPeriod] = Field(default_factory=list)