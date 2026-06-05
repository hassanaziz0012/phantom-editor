"""
Discover API Backend Server

A clean, modular, and secure FastAPI backend server.
Follows premium code guidelines and strict security patterns (CORS limits, localhost binds).
"""

import logging
import os
from typing import List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Set up logging with a clean, descriptive format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("discover_api")

# Load environment variables (searches recursively up to find .env file)
load_dotenv()

from discover_api.routes import router as youtube_router

# FastAPI application initialization
app = FastAPI(
    title="Discover API Server",
    description="Backend service for outlier detection and content discovery.",
    version="0.1.0",
)

# Register YouTube router
app.include_router(youtube_router)

# --- SECURE CORS CONFIGURATION ---
# Guideline: Avoid wildcard (*) CORS origins. Read from environment with a safe default.
default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",  # Vite dev server default
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

env_origins = os.getenv("ALLOWED_ORIGINS")
if env_origins:
    # Split by comma and clean up whitespace
    allowed_origins = [origin.strip() for origin in env_origins.split(",") if origin.strip()]
    # Ensure wildcard is not mistakenly or dangerously set in production environment
    if "*" in allowed_origins:
        logger.warning(
            "WARNING (Security): Wildcard '*' found in ALLOWED_ORIGINS. "
            "Falling back to secure local development origins for safety."
        )
        allowed_origins = [o for o in allowed_origins if o != "*"]
        if not allowed_origins:
            allowed_origins = default_origins
else:
    allowed_origins = default_origins

logger.info(f"Configuring CORS with allowed origins: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],  # Disable rarely used methods (e.g. TRACE)
    allow_headers=["*"],
)


# --- APPLICATION EVENTS ---
@app.on_event("startup")
async def startup_event():
    """
    Log application startup details and display an elegant console banner.
    """
    banner = """
    ===================================================
                DISCOVER API SERVER STARTED
    ===================================================
    * Status:  Active & Listening
    * Host:    http://127.0.0.1:8000
    * Docs:    http://127.0.0.1:8000/docs
    ===================================================
    """
    for line in banner.strip().split("\n"):
        logger.info(line)


# --- ROUTES ---
@app.get("/", tags=["Root"])
async def root():
    """
    Welcome endpoint returning basic API metadata.
    """
    return {
        "app": "Discover API Server",
        "version": "0.1.0",
        "status": "online",
        "message": "Welcome to the Discover API. Routes and models will be added in subsequent steps.",
    }


@app.get("/health", tags=["Monitoring"])
async def health_check():
    """
    Simple health check endpoint for monitoring tools.
    """
    return {
        "status": "healthy",
        "service": "discover_api",
        "environment": os.getenv("ENV", "development"),
    }
