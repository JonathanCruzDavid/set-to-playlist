"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root first, then fall back to the CWD.
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv()

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def _env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    youtube_api_key: str | None
    spotify_client_id: str | None
    spotify_client_secret: str | None
    spotify_redirect_uri: str
    flask_secret_key: str | None
    spotify_cache_path: str
    unmatched_dir: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            youtube_api_key=_env("YOUTUBE_API_KEY"),
            spotify_client_id=_env("SPOTIPY_CLIENT_ID"),
            spotify_client_secret=_env("SPOTIPY_CLIENT_SECRET"),
            spotify_redirect_uri=_env("SPOTIPY_REDIRECT_URI") or DEFAULT_REDIRECT_URI,
            flask_secret_key=_env("FLASK_SECRET_KEY"),
            spotify_cache_path=_env("SPOTIFY_CACHE_PATH") or str(PROJECT_ROOT / ".spotify_cache"),
            unmatched_dir=_env("UNMATCHED_DIR") or str(PROJECT_ROOT / "unmatched"),
        )

    def missing_spotify(self) -> list[str]:
        missing = []
        if not self.spotify_client_id:
            missing.append("SPOTIPY_CLIENT_ID")
        if not self.spotify_client_secret:
            missing.append("SPOTIPY_CLIENT_SECRET")
        return missing

    def require_spotify(self) -> None:
        missing = self.missing_spotify()
        if missing:
            raise ConfigError(
                "Missing Spotify credentials: " + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in (see README)."
            )
