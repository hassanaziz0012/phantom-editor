"""
Database Connection Provider & Context Manager
===============================================
Responsible only for establishing and yielding PostgreSQL connections.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

import psycopg
from psycopg.rows import dict_row

from .config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)
from .docker import ensure_postgres_container

logger = logging.getLogger("phantom.db.connection")


def get_connection_uri() -> str:
    """Returns PostgreSQL connection URI."""
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"


@contextmanager
def get_db_connection(auto_start: bool = True) -> Generator[psycopg.Connection, None, None]:
    """
    Context manager for obtaining a database connection with dictionary row factory.
    Automatically starts the Docker container if needed and auto_start is True.
    """
    try:
        conn = psycopg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            row_factory=dict_row,
        )
    except psycopg.OperationalError:
        if auto_start:
            logger.warning("Could not connect to PostgreSQL. Attempting to start container...")
            if ensure_postgres_container():
                conn = psycopg.connect(
                    host=POSTGRES_HOST,
                    port=POSTGRES_PORT,
                    dbname=POSTGRES_DB,
                    user=POSTGRES_USER,
                    password=POSTGRES_PASSWORD,
                    row_factory=dict_row,
                )
            else:
                raise
        else:
            raise

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
