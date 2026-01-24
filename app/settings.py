from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Metadata Enrichment API"
    require_api_key: bool = True
    api_key: str = ""

    # Rate limit, e.g. "60/minute"
    rate_limit_default: str = "60/minute"

    # Middleware
    cors_allow_origins: str = "*"  # comma-separated
    cors_allow_credentials: bool = False
    cors_allow_methods: str = "*"  # comma-separated
    cors_allow_headers: str = "*"  # comma-separated
    enable_gzip: bool = True

    # Metrics
    enable_metrics: bool = True

    # RapidAPI compatibility
    # If set, requests that include X-RapidAPI-Key will be accepted (optionally requiring the proxy secret).
    require_rapidapi_proxy_secret: bool = False
    rapidapi_proxy_secret: str = ""

    # Outbound HTTP
    http_timeout_seconds: float = 12.0
    http_user_agent: str = "metadata-api/1.0 (+https://example.com)"

    # YouTube Data API v3
    youtube_api_key: str = ""
    youtube_api_base: str = "https://www.googleapis.com/youtube/v3"


settings = Settings()
