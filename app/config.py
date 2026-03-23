from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )

    # Anthropic
    anthropic_api_key: str

    # FAA NOTAM API
    faa_client_id: str = ""
    faa_client_secret: str = ""

    # Database
    database_url: str = ""

    # Agent behavior
    weather_cache_ttl_minutes: int = 60
    notam_cache_ttl_minutes: int = 30
    llm_model: str = "claude-sonnet-4-20250514"

    # smarter agent
    use_react_analyzer: bool = False

# Single instance imported everywhere
settings = Settings()