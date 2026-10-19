"""Application configuration and environment settings for Order Ops Console."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_DEMO_REFERENCE_ISO = "2026-10-19T18:00:00+05:30"


class AppConfig(BaseModel):
    """Centralized configuration for both Demo Mode (local SQLite) and Connected Mode (Supabase + Gemini)."""

    app_mode: str = Field(default_factory=lambda: os.getenv("APP_MODE", "demo").strip().lower())
    default_workspace_id: str = Field(
        default_factory=lambda: os.getenv("DEFAULT_WORKSPACE_ID", "demo-workspace").strip()
    )
    reference_time_mode: str = Field(
        default_factory=lambda: os.getenv("REFERENCE_TIME_MODE", "demo_fixed").strip().lower()
    )
    demo_reference_time: str = Field(
        default_factory=lambda: os.getenv("DEMO_REFERENCE_TIME", DEFAULT_DEMO_REFERENCE_ISO).strip()
    )
    database_url: str = Field(default_factory=lambda: os.getenv("DATABASE_URL", "").strip())
    supabase_url: str = Field(default_factory=lambda: os.getenv("SUPABASE_URL", "").strip())
    supabase_anon_key: str = Field(default_factory=lambda: os.getenv("SUPABASE_ANON_KEY", "").strip())
    supabase_jwt_secret: str = Field(default_factory=lambda: os.getenv("SUPABASE_JWT_SECRET", "").strip())

    require_auth: bool = Field(
        default_factory=lambda: os.getenv("REQUIRE_AUTH", "false").strip().lower() in ("1", "true", "yes")
    )
    app_access_token: str = Field(default_factory=lambda: os.getenv("APP_ACCESS_TOKEN", "").strip())
    max_upload_size_mb: int = Field(
        default_factory=lambda: int(os.getenv("MAX_UPLOAD_SIZE_MB", "10").strip() or "10")
    )

    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = Field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    )
    gemini_timeout_seconds: int = Field(
        default_factory=lambda: int(os.getenv("GEMINI_TIMEOUT_SECONDS", "8").strip() or "8")
    )

    threshold_unshipped_hours: int = Field(
        default_factory=lambda: int(os.getenv("THRESHOLD_UNSHIPPED_HOURS", "48").strip() or "48")
    )
    threshold_stale_tracking_hours: int = Field(
        default_factory=lambda: int(os.getenv("THRESHOLD_STALE_TRACKING_HOURS", "72").strip() or "72")
    )

    @property
    def is_postgres_configured(self) -> bool:
        return bool(self.database_url and self.database_url.startswith(("postgresql", "postgres")))

    @property
    def is_supabase_configured(self) -> bool:
        return bool(self.is_postgres_configured or (self.supabase_url and self.supabase_anon_key))

    @property
    def is_gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            if self.database_url.startswith("postgres://"):
                return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
            return self.database_url
        data_dir = BASE_DIR / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "order_ops_console.db"
        return f"sqlite:///{db_path.as_posix()}"

    def get_default_reference_utc(self) -> datetime:
        """Return the reference timestamp in UTC based on configured mode."""
        if self.reference_time_mode == "live_ist":
            return datetime.now(timezone.utc)
        try:
            dt = datetime.fromisoformat(self.demo_reference_time)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.astimezone(timezone.utc)
        except ValueError:
            dt = datetime.fromisoformat(DEFAULT_DEMO_REFERENCE_ISO)
            return dt.astimezone(timezone.utc)


def get_config() -> AppConfig:
    return AppConfig()
