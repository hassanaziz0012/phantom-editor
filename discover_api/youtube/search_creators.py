"""
YouTube Creator Search Logic
============================
Handles searching YouTube for creators/channels using the YouTube Data API v3.
"""

import logging
from typing import List, Dict, Any
from .utils import get_youtube_client

logger = logging.getLogger("discover_api.youtube.search_creators")

def search_youtube_creators(api_key: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search YouTube for creators matching the query string.
    
    Args:
        api_key: The YouTube API Key.
        query: Search term for the channel.
        limit: Max number of results.
        
    Returns:
        List of dicts containing: channel_id, name, thumbnail_url, description.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    try:
        youtube = get_youtube_client(api_key)
        
        # Execute the search query for type="channel"
        response = youtube.search().list(
            part="snippet",
            q=clean_query,
            type="channel",
            maxResults=limit
        ).execute()

        results = []
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            channel_id = item.get("id", {}).get("channelId") or snippet.get("channelId")
            if not channel_id:
                continue

            thumbnails = snippet.get("thumbnails", {})
            thumbnail_url = ""
            for quality in ("high", "medium", "default"):
                if quality in thumbnails:
                    thumbnail_url = thumbnails[quality]["url"]
                    break

            results.append({
                "channel_id": channel_id,
                "name": snippet.get("title", ""),
                "thumbnail_url": thumbnail_url,
                "description": snippet.get("description", ""),
            })

        return results
    except Exception as e:
        logger.error(f"Error searching creators with YouTube API: {e}", exc_info=True)
        raise e
