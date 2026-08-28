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
    celery_visibility_timeout_seconds: int = Field(
        default=12 * 60 * 60,
        ge=60 * 60,
    )
    

    ml_client_id: str | None = None
    ml_client_secret: str | None = None
    ml_redirect_uri: str | None = None

    ml_auth_url: str = "https://auth.mercadolibre.cl/authorization"
    ml_token_url: str = "https://api.mercadolibre.com/oauth/token"
    ml_me_url: str = "https://api.mercadolibre.com/users/me"
    ml_api_base: str = "https://api.mercadolibre.com"
    ml_domain_id: str = "MLC-CARS_AND_VANS_FOR_COMPATIBILITIES"
    ml_site_id: str = "MLC"
    seller_sales_timezone: str = "America/Santiago"
    seller_sales_page_limit: int = Field(default=50, ge=1, le=50)
    seller_sales_note_concurrency: int = Field(default=4, ge=1, le=10)
    ml_notifications_enabled: bool = True
    ml_notification_application_id: str | None = None
    ml_notification_allowed_user_id: str | None = None
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
    supabase_publications_table: str = "publicaciones_ml"
    supabase_meli_notifications_table: str = "meli_notification_events"
    supabase_meli_packs_table: str = "meli_packs"
    supabase_meli_orders_table: str = "meli_orders"
    supabase_meli_order_items_table: str = "meli_order_items"
    supabase_meli_shipments_table: str = "meli_shipments"
    supabase_meli_order_shipments_table: str = "meli_order_shipments"
    backend_auth_enabled: bool = True
    backend_auth_cache_ttl_seconds: int = 60

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
    ml_write_requests_per_second: float = 100 / 60
    ml_write_max_requests_per_window: int = 100
    ml_write_window_seconds: int = 60
    ml_write_cooldown_seconds: float = 120.0
    ml_compatibility_write_requests_per_second: float = 100 / 60
    ml_compatibility_max_requests_per_window: int = 100
    ml_compatibility_window_seconds: int = 60
    ml_compatibility_cooldown_seconds: float = 120.0
    ml_compatibility_exception_write_requests_per_second: float = 100 / 60
    ml_compatibility_exception_max_requests_per_window: int = 100
    ml_compatibility_exception_window_seconds: int = 60
    ml_compatibility_exception_cooldown_seconds: float = 120.0
    ml_price_stock_write_requests_per_second: float = 100 / 60
    ml_price_stock_max_requests_per_window: int = 100
    ml_price_stock_window_seconds: int = 60
    ml_price_stock_cooldown_seconds: float = 120.0
    ml_retry_max_delay_seconds: float = 60.0
    ml_retry_429_min_delay_seconds: float = 12.0
    ml_retry_429_cooldown_seconds: float = 30.0
    ml_retry_unknown_403_min_delay_seconds: float = 5.0
    ml_retry_unknown_403_cooldown_seconds: float = 15.0

    # Exportacion de publicaciones con descripciones.
    ml_publication_export_requests_per_second: float = Field(
        default=2.0,
        ge=0.1,
        le=10.0,
    )
    ml_publication_export_concurrency: int = Field(
        default=5,
        ge=1,
        le=20,
    )
    publication_export_worker_concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
    )
    ml_publication_export_batch_size: int = Field(
        default=50,
        ge=1,
        le=500,
    )
    ml_publication_description_cache_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        ge=5 * 60,
    )
    ml_publication_missing_cache_ttl_seconds: int = Field(
        default=6 * 60 * 60,
        ge=5 * 60,
    )
    ml_publication_export_artifact_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        ge=60 * 60,
    )
    ml_publication_export_task_max_retries: int = Field(
        default=5,
        ge=0,
        le=20,
    )
    ml_publication_export_retry_base_delay_seconds: int = Field(
        default=60,
        ge=5,
    )
    ml_publication_export_retry_max_delay_seconds: int = Field(
        default=15 * 60,
        ge=60,
    )
    ml_publication_export_lock_ttl_seconds: int = Field(
        default=2 * 60,
        ge=60,
    )
    ml_publication_export_lock_heartbeat_seconds: int = Field(
        default=30,
        ge=10,
    )
    ml_publication_export_recovery_stale_seconds: int = Field(
        default=3 * 60,
        ge=60,
    )

    # Procesamiento
    max_row_concurrency: int = 2
    job_progress_update_every: int = 25

    compat_batch_size: int = 100
    compat_batch_concurrency: int = 2
    process_file_chunk_size: int = 100
    process_file_chunk_pause_seconds: int = 2 * 60
    compatibility_chunk_pause_seconds: int = 3 * 60 + 30
    compatibility_exception_chunk_pause_seconds: int = 2 * 60
    price_stock_chunk_pause_seconds: int = 2 * 60
    item_pictures_chunk_pause_seconds: int = 2 * 60
    sku_description_chunk_pause_seconds: int = 2 * 60
    process_queue_delay_seconds: int = 15 * 60

    token_refresh_margin_seconds: int = 600

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
