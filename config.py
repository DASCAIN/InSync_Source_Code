"""
Application configuration using pydantic-settings.
Loads settings from .env file and provides directory management.
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
# Force load .env and override OS environment variables to prevent stale shell caches
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(env_path, override=True)

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    LLM_MODEL: str = "gpt-4o-mini"
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 50
    MAX_UPLOAD_SIZE_MB: int = 50

    # HeyGen API Settings
    HEYGEN_API_KEY: str = ""
    HEYGEN_AVATAR_ID: str = "Daisy_public_20220914"
    HEYGEN_VOICE_ID: str = "114a2824b75f49639e1556ae8cb45b81"

    # Directory paths
    BASE_DIR: Path = Path(__file__).resolve().parent
    DATA_DIR: Path = BASE_DIR / "data"
    UPLOADS_DIR: Path = DATA_DIR / "uploads"
    VECTOR_STORES_DIR: Path = DATA_DIR / "vector_stores"
    VIDEOS_DIR: Path = DATA_DIR / "videos"
    GENERATED_DIR: Path = DATA_DIR / "generated_content"
    METADATA_FILE: Path = DATA_DIR / "metadata.json"

    class Config:
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


def ensure_directories() -> None:
    """Create required directories on startup if they don't exist."""
    settings = get_settings()
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    settings.VECTOR_STORES_DIR.mkdir(parents=True, exist_ok=True)
    settings.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # Create metadata.json if it doesn't exist
    if not settings.METADATA_FILE.exists():
        import json

        with open(settings.METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)


@lru_cache()
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
