"""Configuration loaded from environment variables (.env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return val


@dataclass(frozen=True)
class GarminConfig:
    email: str
    password: str


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    email: str
    password: str
    notify_email: str


@dataclass(frozen=True)
class AppConfig:
    garmin: GarminConfig
    smtp: SMTPConfig | None = None
    poll_interval_minutes: int = 30


def load_config() -> AppConfig:
    """Load config. SMTP settings are optional (not needed for CLI testing)."""
    smtp = None
    if os.getenv("SMTP_EMAIL"):
        smtp = SMTPConfig(
            host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            port=int(os.getenv("SMTP_PORT", "587")),
            email=_require("SMTP_EMAIL"),
            password=_require("SMTP_PASSWORD"),
            notify_email=_require("NOTIFY_EMAIL"),
        )

    return AppConfig(
        garmin=GarminConfig(
            email=_require("GARMIN_EMAIL"),
            password=_require("GARMIN_PASSWORD"),
        ),
        smtp=smtp,
        poll_interval_minutes=int(os.getenv("POLL_INTERVAL_MINUTES", "30")),
    )
