from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

class Settings(BaseSettings):

    app_env: str = "development"
    frontend_url: str = "http://localhost:5173"

    #upload_dir: str = "uploads"
    #tokens_file: str = "tokens.json"

    upload_dir: str = str(BASE_DIR / "uploads")
    tokens_file: str = str(BASE_DIR / "tokens.json")

    redis_url: str = "redis://redis:6379/0"
    

    ml_client_id: str | None = None
    ml_client_secret: str | None = None
    ml_redirect_uri: str | None = None

    ml_auth_url: str = "https://auth.mercadolibre.cl/authorization"
    ml_token_url: str = "https://api.mercadolibre.com/oauth/token"
    ml_me_url: str = "https://api.mercadolibre.com/users/me"
    ml_api_base: str = "https://api.mercadolibre.com"
    ml_domain_id: str = "MLC-CARS_AND_VANS_FOR_COMPATIBILITIES"
    ml_site_id: str = "MLC"
    supabase_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPABASE_URL", "VITE_SUPABASE_URL"),
    )
    supabase_anon_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SUPABASE_ANON_KEY", "VITE_SUPABASE_ANON_KEY"),
    )
    supabase_service_role_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_SERVICE_KEY",
        ),
    )
    supabase_meli_connection_table: str = "meli_global_connection"
    supabase_process_table: str = "procesos"

    ml_compatibility_exception_comment: str = (
        "No aparecen detalles técnicos del modelo correspondiente."
    )

    # HTTP client
    ml_http_timeout: float = 30.0
    ml_http_max_connections: int = 20
    ml_http_max_keepalive: int = 10

    # Retry / rate limit (ML API limit: 100 rpm por APP_ID)
    ml_retry_attempts: int = 6
    ml_retry_base_delay: float = 1.0
    ml_requests_per_second: float = 1.5
    ml_read_requests_per_second: float = 0.8
    ml_write_requests_per_second: float = 0.35
    ml_compatibility_write_requests_per_second: float = 0.25
    ml_compatibility_exception_write_requests_per_second: float = 0.15
    ml_price_stock_write_requests_per_second: float = 0.35
    ml_retry_max_delay_seconds: float = 60.0
    ml_retry_429_min_delay_seconds: float = 12.0
    ml_retry_429_cooldown_seconds: float = 30.0

    # Procesamiento
    max_row_concurrency: int = 2
    job_progress_update_every: int = 25

    compat_batch_size: int = 200
    compat_batch_concurrency: int = 2
    process_file_chunk_size: int = 100
    compatibility_chunk_pause_seconds: int = 18 * 60
    compatibility_exception_chunk_pause_seconds: int = 18 * 60
    price_stock_chunk_pause_seconds: int = 6 * 60
    process_queue_delay_seconds: int = 15 * 60

    token_refresh_margin_seconds: int = 600

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
