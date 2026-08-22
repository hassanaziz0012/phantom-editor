"""
Database Schema Definition & Initialization
============================================
Responsible only for table schemas, indexes, and database migrations.
"""

from __future__ import annotations

import logging
from .connection import get_db_connection

logger = logging.getLogger("phantom.db.schema")

SCHEMA_SQL = """
-- Create channels table
CREATE TABLE IF NOT EXISTS channels (
    channel_id VARCHAR(64) PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    custom_url TEXT,
    published_at TIMESTAMPTZ,
    subscriber_count BIGINT DEFAULT 0,
    video_count INTEGER DEFAULT 0,
    total_view_count BIGINT DEFAULT 0,
    avg_views DOUBLE PRECISION DEFAULT 0.0,
    avg_likes DOUBLE PRECISION DEFAULT 0.0,
    thumbnail_url TEXT,
    country VARCHAR(16),
    last_scraped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Create videos table
CREATE TABLE IF NOT EXISTS videos (
    video_id VARCHAR(32) PRIMARY KEY,
    channel_id VARCHAR(64) NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    published_at TIMESTAMPTZ,
    duration VARCHAR(32),
    duration_seconds INTEGER,
    thumbnail_url TEXT,
    tags TEXT[] DEFAULT '{}',
    category_id VARCHAR(16),
    url TEXT NOT NULL,
    view_count BIGINT DEFAULT 0,
    like_count BIGINT DEFAULT 0,
    comment_count BIGINT DEFAULT 0,
    is_short BOOLEAN DEFAULT FALSE,
    summary TEXT,
    takeaways TEXT[] DEFAULT '{}',
    
    -- Scoring metrics
    view_score DOUBLE PRECISION DEFAULT 0.0,
    like_score DOUBLE PRECISION DEFAULT 0.0,
    base_score DOUBLE PRECISION DEFAULT 0.0,
    score DOUBLE PRECISION DEFAULT 0.0,
    is_outlier BOOLEAN DEFAULT FALSE,
    
    last_scraped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance & rapid query execution
CREATE INDEX IF NOT EXISTS idx_videos_channel_id ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_published_at ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_view_count ON videos(view_count DESC);
CREATE INDEX IF NOT EXISTS idx_videos_score ON videos(score DESC);
CREATE INDEX IF NOT EXISTS idx_videos_view_score ON videos(view_score DESC);
CREATE INDEX IF NOT EXISTS idx_videos_is_outlier ON videos(is_outlier);
CREATE INDEX IF NOT EXISTS idx_videos_is_short ON videos(is_short);

-- Auto-update updated_at timestamp trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_channels_updated_at') THEN
        CREATE TRIGGER set_channels_updated_at
        BEFORE UPDATE ON channels
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'set_videos_updated_at') THEN
        CREATE TRIGGER set_videos_updated_at
        BEFORE UPDATE ON videos
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
"""


def init_db() -> None:
    """Initializes the database schema, required tables, indexes, and migrations."""
    logger.info("Initializing database schema...")
    with get_db_connection(auto_start=True) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            # Automatic column migrations for existing tables
            cur.execute(
                """
                ALTER TABLE videos ADD COLUMN IF NOT EXISTS summary TEXT;
                ALTER TABLE videos ADD COLUMN IF NOT EXISTS takeaways TEXT[] DEFAULT '{}';
                """
            )
    logger.info("Database schema initialized successfully.")
