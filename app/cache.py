from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Session
from app.config import settings

# Ensure SQLAlchemy uses psycopg v3 for postgres URLs
_db_url = settings.database_url
if _db_url.startswith("postgresql://") or _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    _db_url = _db_url.replace("postgres://", "postgresql+psycopg://", 1)

engine = create_engine(_db_url)


class Base(DeclarativeBase):
    pass


class WeatherCache(Base):
    __tablename__ = "weather_cache"

    key = Column(String(64), primary_key=True)   # e.g. "metar:KMMU"
    data = Column(Text, nullable=False)           # JSON string
    cached_at = Column(DateTime, nullable=False)


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    Base.metadata.create_all(engine)


def get_cached(key: str, ttl_minutes: int) -> str | None:
    """
    Return cached JSON string if it exists and is within TTL.
    Returns None if missing or stale.
    """
    with Session(engine) as session:
        row = session.get(WeatherCache, key)
        if row is None:
            return None

        age_minutes = (
            datetime.now(timezone.utc) - row.cached_at.replace(tzinfo=timezone.utc)
        ).total_seconds() / 60

        if age_minutes > ttl_minutes:
            return None

        return row.data


def set_cached(key: str, data: str):
    """Write or overwrite a cache entry."""
    with Session(engine) as session:
        row = session.get(WeatherCache, key)
        if row:
            row.data = data
            row.cached_at = datetime.now(timezone.utc)
        else:
            row = WeatherCache(
                key=key,
                data=data,
                cached_at=datetime.now(timezone.utc),
            )
            session.add(row)
        session.commit()


def clear_cache(icao: str | None = None):
    """Clear cache entries. Pass icao to clear one airport, or None to clear all."""
    with Session(engine) as session:
        if icao:
            for prefix in ("metar:", "taf:", "notam:"):
                row = session.get(WeatherCache, f"{prefix}{icao.upper()}")
                if row:
                    session.delete(row)
        else:
            session.query(WeatherCache).delete()
        session.commit()