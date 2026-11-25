import os
from dataclasses import dataclass


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("ENV", "dev")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./medtime.db")
    secret_key: str = os.getenv("SECRET_KEY", "dev-only-secret-change-me")
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "720"))
    cors_origins: tuple = tuple(
        o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if o.strip()
    )
    missed_grace_minutes: int = int(os.getenv("MISSED_GRACE_MINUTES", "120"))
    late_after_minutes: int = int(os.getenv("LATE_AFTER_MINUTES", "30"))
    dose_horizon_days: int = int(os.getenv("DOSE_HORIZON_DAYS", "14"))
    auto_decrement_stock: bool = _b("AUTO_DECREMENT_STOCK", False)
    run_scheduler: bool = _b("RUN_SCHEDULER", True)
    otp_max_per_window: int = int(os.getenv("OTP_MAX_PER_15_MIN", "5"))
    otp_ttl_seconds: int = 300


settings = Settings()
