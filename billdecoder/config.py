"""Environment-driven configuration with a hard allow-list of competition models."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv is in requirements.txt, but never crash if it is missing
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ALLOWED_MODELS: dict[str, set[str]] = {
    "google": {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"},
}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when configuration is missing or uses a non-allowed model."""


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    api_key: str
    workers: int = 1
    min_request_interval: float = 4.0  # seconds between API calls (global)
    max_retries: int = 4
    max_image_side: int = 2200
    cache_dir: str = ".cache"


def load_settings(require_key: bool = True) -> Settings:
    if load_dotenv is not None:
        dotenv_path = _PROJECT_ROOT / ".env"
        if dotenv_path.exists():
            load_dotenv(dotenv_path=dotenv_path, override=False)
    provider = os.getenv("MODEL_PROVIDER", "google").strip().lower()
    model = os.getenv("MODEL_NAME", "gemini-3.5-flash-lite").strip()
    api_key = os.getenv("API_KEY", "").strip()

    if provider not in ALLOWED_MODELS or model not in ALLOWED_MODELS[provider]:
        allowed = ", ".join(f"{p}:{m}" for p, ms in ALLOWED_MODELS.items() for m in sorted(ms))
        raise ConfigError(
            f"Model '{provider}:{model}' is not allowed in this competition. Allowed: {allowed}"
        )
    placeholder = api_key.lower().startswith(("your-", "paste-")) or "api-key-here" in api_key.lower()
    if require_key and (not api_key or placeholder):
        raise ConfigError("API_KEY is not set. Copy .env.example to .env and add your key.")

    try:
        workers = int(os.getenv("WORKERS", "1"))
        min_request_interval = float(os.getenv("MIN_REQUEST_INTERVAL", "4"))
        max_retries = int(os.getenv("MAX_RETRIES", "4"))
        max_image_side = int(os.getenv("MAX_IMAGE_SIDE", "2200"))
    except ValueError as exc:
        raise ConfigError(f"Invalid numeric configuration: {exc}") from exc
    if workers < 1:
        raise ConfigError("WORKERS must be at least 1.")
    if not math.isfinite(min_request_interval) or min_request_interval < 0:
        raise ConfigError("MIN_REQUEST_INTERVAL must be a finite number greater than or equal to 0.")
    if max_retries < 0:
        raise ConfigError("MAX_RETRIES must be greater than or equal to 0.")
    if max_image_side < 1:
        raise ConfigError("MAX_IMAGE_SIDE must be at least 1.")

    return Settings(
        provider=provider,
        model=model,
        api_key=api_key,
        workers=workers,
        min_request_interval=min_request_interval,
        max_retries=max_retries,
        max_image_side=max_image_side,
        cache_dir=os.getenv("CACHE_DIR", ".cache").strip() or ".cache",
    )