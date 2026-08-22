"""
Docker Container Management for PostgreSQL
===========================================
Responsible only for inspecting, starting, stopping, and verifying
the PostgreSQL Docker container (postgres:18.6-alpine).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import Optional

import psycopg

from .config import (
    POSTGRES_CONTAINER_NAME,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    POSTGRES_VOLUME,
)

logger = logging.getLogger("phantom.db.docker")


def is_docker_installed() -> bool:
    """Check if Docker CLI is available on the host machine."""
    return shutil.which("docker") is not None


def get_container_status(container_name: str = POSTGRES_CONTAINER_NAME) -> Optional[str]:
    """
    Returns container status string (e.g. 'running', 'exited', 'created')
    or None if the container does not exist.
    """
    if not is_docker_installed():
        raise RuntimeError("Docker is not installed or not in PATH.")

    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception as exc:
        logger.error("Error inspecting container %s: %s", container_name, exc)
        return None


def ensure_postgres_container(
    container_name: str = POSTGRES_CONTAINER_NAME,
    image: str = POSTGRES_IMAGE,
    port: int = POSTGRES_PORT,
    db_name: str = POSTGRES_DB,
    user: str = POSTGRES_USER,
    password: str = POSTGRES_PASSWORD,
    volume_name: str = POSTGRES_VOLUME,
    timeout_seconds: int = 30,
) -> bool:
    """
    Ensures that a PostgreSQL Docker container is running and healthy.
    Creates and starts it if it does not exist, or resumes it if stopped.
    """
    if not is_docker_installed():
        logger.error("Docker command-line tool is not found. Please install Docker.")
        return False

    status = get_container_status(container_name)

    if status == "running":
        logger.info("PostgreSQL container '%s' is already running.", container_name)
    elif status is not None:
        logger.info("Container '%s' exists with status '%s'. Starting it...", container_name, status)
        start_res = subprocess.run(["docker", "start", container_name], capture_output=True, text=True)
        if start_res.returncode != 0:
            logger.error("Failed to start container '%s': %s", container_name, start_res.stderr.strip())
            return False
    else:
        logger.info(
            "Creating and running PostgreSQL container '%s' with image '%s' on port %d...",
            container_name,
            image,
            port,
        )
        run_cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-p",
            f"{port}:5432",
            "-e",
            f"POSTGRES_DB={db_name}",
            "-e",
            f"POSTGRES_USER={user}",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-v",
            f"{volume_name}:/var/lib/postgresql",
            "--restart",
            "unless-stopped",
            image,
        ]
        res = subprocess.run(run_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Failed to run Docker container: %s", res.stderr.strip())
            return False
        logger.info("Container created successfully: %s", res.stdout.strip()[:12])

    # Wait for PostgreSQL to become ready
    logger.info("Waiting for PostgreSQL database to be ready on port %d...", port)
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            with psycopg.connect(
                host=POSTGRES_HOST,
                port=port,
                dbname=db_name,
                user=user,
                password=password,
                connect_timeout=3,
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    if cur.fetchone():
                        logger.info("Database connection established successfully!")
                        return True
        except Exception:
            time.sleep(1.0)

    logger.error("Timed out waiting for PostgreSQL to start after %d seconds.", timeout_seconds)
    return False


def stop_postgres_container(container_name: str = POSTGRES_CONTAINER_NAME) -> bool:
    """Stops the running PostgreSQL Docker container."""
    if not is_docker_installed():
        return False
    res = subprocess.run(["docker", "stop", container_name], capture_output=True, text=True)
    if res.returncode == 0:
        logger.info("Container '%s' stopped.", container_name)
        return True
    logger.error("Failed to stop container '%s': %s", container_name, res.stderr.strip())
    return False
