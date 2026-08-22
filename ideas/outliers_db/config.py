"""
Database Configuration & Environment Settings
==============================================
Responsible only for loading environment variables and defining
database and Docker container configuration settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

POSTGRES_CONTAINER_NAME: str = os.getenv("POSTGRES_CONTAINER_NAME", "phantom-postgres")
POSTGRES_IMAGE: str = os.getenv("POSTGRES_IMAGE", "postgres:18.6-alpine")
POSTGRES_DB: str = os.getenv("POSTGRES_DB", "phantom_ideas")
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "postgres")
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_VOLUME: str = os.getenv("POSTGRES_VOLUME", "phantom_postgres_data")
