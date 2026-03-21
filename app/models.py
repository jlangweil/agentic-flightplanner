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