"""Runtime settings: read from the environment and an optional .env file."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    load_dotenv(Path.home() / ".papassist.env")
except Exception:  # pragma: no cover
    pass

DEFAULT_MODEL = "claude-opus-5"


class Settings:
    def __init__(self) -> None:
        self.host = os.environ.get("PAPASSIST_HOST", "127.0.0.1")
        self.port = int(os.environ.get("PAPASSIST_PORT", "8765"))
        self.model = os.environ.get("PAPASSIST_MODEL", DEFAULT_MODEL)
        self.llm_enabled = os.environ.get("PAPASSIST_LLM", "auto").lower() not in ("0", "off", "false", "no")
        self.auto_enrich = os.environ.get("PAPASSIST_AUTO_ENRICH", "1").lower() not in ("0", "off", "false", "no")

    @property
    def api_key_present(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def to_dict(self) -> dict:
        return {
            "model": self.model, "llm_enabled": self.llm_enabled, "api_key_present": self.api_key_present,
            "auto_enrich": self.auto_enrich, "llm_active": self.llm_enabled and self.api_key_present,
        }


settings = Settings()
