"""
Studio's Content tab: the channel's videos, Shorts, live streams, playlists and podcasts,
one video's full details, and comments, all read from Studio's internal API.

Field masks (which fields Studio returns) are the ones Studio's own pages send, saved in
masks.json, so every list has everything Studio shows, including drafts, private and
scheduled videos.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from youtube_api.studio.session import Studio, StudioError

MASKS = json.loads((Path(__file__).parent / "masks.json").read_text())
PAGE_SIZE = 100

# kind -> filter operands besides the channel (the same filters as Studio's Content tabs)
_NOT_TVFILM = [{"not": {"operand": {"tvfilmTypeIs": {"value": f"VIDEO_TVFILM_TYPE_{t}"}}}} for t in ("MOVIE", "EPISODE", "EVENT")]
KINDS = {
    "videos": [{"and": {"operands": [{"videoOriginIs": {"value": "VIDEO_ORIGIN_UPLOAD"}},
                                     {"not": {"operand": {"contentTypeIs": {"value": "CREATOR_CONTENT_TYPE_SHORTS"}}}}]}}] + _NOT_TVFILM,
    "shorts": [{"contentTypeIs": {"value": "CREATOR_CONTENT_TYPE_SHORTS"}}],
    "live":   [{"videoOriginIs": {"value": "VIDEO_ORIGIN_LIVESTREAM"}}],
    "all":    [],
}


def _iso(seconds) -> Optional[str]:
    return datetime.fromtimestamp(int(seconds)).isoformat(timespec="minutes") if seconds else None


def _int(value) -> Optional[int]:
    return int(value) if value not in (None, "") else None


def _experiment(v: dict) -> Optional[dict]:
    """A Test & Compare (thumbnail/title A/B test): its state, result and each variant's title."""
    e = v.get("videoCreatorExperiment") or {}
    if not e.get("state"):
        return None
    result = e.get("result") or {}
    return {
        "state": e["state"].replace("CREATOR_EXPERIMENT_STATE_", "").lower(),
        "result": (result.get("resultState") or "").replace("CREATOR_EXPERIMENT_RESULT_STATE_", "").lower() or None,
        "winner": (result.get("winnerArm") or "").replace("CREATOR_EXPERIMENT_ARM_", "").lower().replace("unspecified", "") or None,
        "started": _iso((e.get("experimentStartTime") or {}).get("seconds")),
        "variants": ["".join(seg.get("text", "") for seg in (arm.get("title") or {}).get("textSegments", [])) or None
                     for arm in e.get("experimentArmData", [])],
    }


def summarize_video(v: dict) -> dict:
    """The fields worth reading from a Studio video record (--raw keeps all of them)."""
    metrics = v.get("publicMetrics") or {}
    scheduled = (v.get("scheduledPublishingDetails") or {}).get("scheduledPublishings") or []
    return {
        "video_id": v.get("videoId"),
        "title": v.get("title"),
        "type": (v.get("contentType") or "").replace("CREATOR_CONTENT_TYPE_", "").lower() or None,
        "privacy": (v.get("privacy") or "").replace("VIDEO_PRIVACY_", "").lower() or None,
        "status": (v.get("status") or "").replace("VIDEO_STATUS_", "").lower() or None,
        "draft": (v.get("draftStatus") or "") not in ("", "DRAFT_STATUS_NONE"),
        "published": _iso(v.get("timePublishedSeconds")),
        "created": _iso(v.get("timeCreatedSeconds")),
        "scheduled": _iso(scheduled[0].get("timeSeconds")) if scheduled else None,
        "length_sec": _int(v.get("lengthSeconds")),
        "views": _int(metrics.get("externalViewCount")),
        "likes": _int(metrics.get("likeCount")),
        "comments": _int(metrics.get("commentCount")),
        "monetization": (((v.get("monetization") or {}).get("adMonetization") or {}).get("effectiveStatus") or "").replace("VIDEO_MONETIZING_STATUS_", "").lower().split("_channel_")[0] or None,
        "restrictions": [r.get("reason") or r.get("type") for r in (v.get("allRestrictions") or {}).get("restrictions", [])] or None,
        "thumbnail": ((v.get("thumbnailDetails") or {}).get("thumbnails") or [{}])[-1].get("url"),
        "experiment": _experiment(v),
    }


def list_videos(studio: Studio, kind: str = "videos", limit: Optional[int] = None, raw: bool = False) -> list[dict]:
    """Everything on one of Studio's Content tabs (videos, shorts, live) or all of them, newest first."""
    operands = [{"channelIdIs": {"value": studio.channel_id}}] + KINDS[kind]
    body = {
        "filter": {"and": {"operands": operands}},
        "order": "VIDEO_ORDER_DISPLAY_TIME_DESC",
        "pageSize": PAGE_SIZE,
        "mask": MASKS["list_videos"],
    }
    videos = []
    while True:
        response = studio.call("creator/list_creator_videos", body)
        videos.extend(response.get("videos", []))
        token = response.get("nextPageToken")
        if not token or (limit and len(videos) >= limit):
            break
        body["pageToken"] = token
    videos = videos[:limit] if limit else videos
    return videos if raw else [summarize_video(v) for v in videos]


def get_videos(studio: Studio, video_ids: list[str], raw: bool = False) -> list[dict]:
    """Full Studio records for some videos (title, description, tags, settings, status, metrics...)."""
    response = studio.call("creator/get_creator_videos", {"videoIds": list(video_ids), "mask": MASKS["video_details"]})
    videos = response.get("videos", [])
    for v in videos:
        status = (v.get("responseStatus") or {}).get("statusCode")
        if status and status != "CREATOR_ENTITY_STATUS_OK":
            raise StudioError(f"{v.get('videoId')}: {status}")
    if raw:
        return videos
    out = []
    for v in videos:
        summary = summarize_video(v)
        summary.update({
            "description": v.get("description"),
            "tags": (v.get("tags") or {}).get("values"),
            "category": v.get("category"),
            "audio_language": (v.get("audioLanguage") or {}).get("value"),
            "made_for_kids": (v.get("mfkSettings") or {}).get("effectiveMfk", "").endswith("_MFK") and not (v.get("mfkSettings") or {}).get("effectiveMfk", "").endswith("NOT_MFK"),
            "license": v.get("license"),
            "comments_setting": v.get("commentFilter"),
            "watch_url": v.get("watchUrl"),
        })
        out.append(summary)
    return out


def list_playlists(studio: Studio, podcasts: bool = False, raw: bool = False) -> list[dict]:
    """The channel's playlists, or only its podcasts."""
    body = {
        "channelId": studio.channel_id,
        "pageSize": PAGE_SIZE,
        "includePlaylistsTotalSize": True,
        "mask": MASKS["playlists"],
    }
    if podcasts:
        body["listSettings"] = {"playlistFilter": {"onlyPodcasts": True}}
    playlists = []
    while True:
        response = studio.call("creator/list_creator_playlists", body)
        playlists.extend(response.get("playlists", []))
        token = response.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
    if raw:
        return playlists
    return [{
        "playlist_id": p.get("playlistId"),
        "title": p.get("title"),
        "videos": _int(p.get("videosCount")),
        "views": _int(p.get("viewCount")),
        "visibility": (p.get("visibilitySetting") or "").replace("PLAYLIST_VISIBILITY_", "").replace("CREATOR_", "").lower() or None,
        "updated": _iso((p.get("lastTimeUpdated") or {}).get("seconds")),
        "podcast": bool(p.get("podcastMetadata")),
        "description": p.get("description") or None,
    } for p in playlists]


# ── Comments ──────────────────────────────────────────────────────────────────

def _text(node) -> str:
    if not node:
        return ""
    return node.get("simpleText") or "".join(r.get("text", "") for r in node.get("runs", []))


def _find(obj, key, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            _find(v, key, out)
    elif isinstance(obj, list):
        for x in obj:
            _find(x, key, out)
    return out


def _comment(c: dict) -> dict:
    thumb = ((c.get("videoThumbnail") or {}).get("commentVideoThumbnailRenderer") or {}).get("thumbnail", {}).get("thumbnails", [{}])
    m = re.search(r"/vi/([\w-]{11})/", thumb[0].get("url", "")) if thumb else None
    return {
        "comment_id": c.get("commentId"),
        "video_id": m and m.group(1),
        "author": _text(c.get("authorText")),
        "text": _text(c.get("contentText")),
        "published": _text(c.get("publishedTimeText")),
        "likes": _text(c.get("voteCount")) or None,
        "by_owner": c.get("authorIsChannelOwner"),
    }


def list_comments(
    studio: Studio, video_id: Optional[str] = None, unanswered: bool = False,
    order: str = "newest", limit: Optional[int] = 100,
) -> list[dict]:
    """Published comments on the channel or one video, newest or most relevant first."""
    relevant = order == "relevant"
    comments_filter = {"sortBy": "SORT_BY_MOST_RELEVANT" if relevant else "SORT_BY_NEWEST"}
    if unanswered:
        comments_filter["commentCategoryIn"] = {"values": ["COMMENT_CATEGORY_NOT_ENGAGED"]}
    body = {
        "sortOrder": "MOST_RELEVANT" if relevant else "NEWEST",
        "searchQuery": "",
        "maxReplies": 10,
        "moderationState": "PUBLISHED",
        "commentsFilter": comments_filter,
        **({"videoId": video_id} if video_id else {"channelId": studio.channel_id}),
    }
    comments = []
    while True:
        response = studio.call("comment/get_comments", body)
        threads = _find(response, "commentThreadRenderer", [])
        for t in threads:
            c = _comment(t["comment"]["commentRenderer"])
            replies = _find(t.get("replies"), "commentRenderer", [])
            c["replies"] = [_comment(r) for r in replies]
            comments.append(c)
        tokens = _find(response, "nextContinuationData", [])
        if not threads or not tokens or (limit and len(comments) >= limit):
            break
        body = {"continuation": tokens[0]["continuation"]}
    return comments[:limit] if limit else comments
