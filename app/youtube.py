from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from cachetools import TTLCache

from .settings import settings


_YT_CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{20,}$")
_YT_VIDEO_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")

# Lightweight caching to reduce quota usage and improve latency.
# Cache keys include the full URL + params.
_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=1024, ttl=300)


class YouTubeConfigError(RuntimeError):
    pass


def parse_youtube_video_id(*, video_id: str | None, url: str | None) -> str | None:
    if video_id:
        candidate: str | None = video_id.strip()
    elif url:
        candidate = _video_id_from_url(url)
    else:
        return None

    # Only pass well-formed 11-character IDs on to the YouTube API.
    if candidate and _YT_VIDEO_ID_RE.match(candidate):
        return candidate
    return None


def _video_id_from_url(url: str) -> str | None:
    u = url.strip()
    parsed = urlparse(u)
    host = (parsed.netloc or "").lower()

    # youtu.be/<id>
    if "youtu.be" in host:
        candidate = (parsed.path or "").strip("/")
        return candidate if candidate else None

    # youtube.com/watch?v=<id>
    qs = parse_qs(parsed.query or "")
    if "v" in qs and qs["v"]:
        return qs["v"][0]

    # youtube.com/shorts/<id>
    path_parts = [p for p in (parsed.path or "").split("/") if p]
    if len(path_parts) >= 2 and path_parts[0].lower() in {"shorts", "embed"}:
        return path_parts[1]

    return None


@dataclass(frozen=True)
class ChannelLocator:
    channel_id: str | None = None
    handle: str | None = None  # without '@'
    username: str | None = None
    custom: str | None = None


def _require_youtube_key() -> str:
    if not settings.youtube_api_key:
        raise YouTubeConfigError("YOUTUBE_API_KEY not set")
    return settings.youtube_api_key


def parse_youtube_channel_input(
    *,
    channel_id: str | None,
    handle: str | None,
    username: str | None,
    url: str | None,
) -> ChannelLocator:
    if channel_id:
        cid = channel_id.strip()
        return ChannelLocator(channel_id=cid)

    if handle:
        h = handle.strip()
        if h.startswith("@"):  # accept @handle
            h = h[1:]
        return ChannelLocator(handle=h)

    if username:
        return ChannelLocator(username=username.strip())

    if url:
        u = url.strip()
        parsed = urlparse(u)
        path = (parsed.path or "").strip("/")
        parts = [p for p in path.split("/") if p]
        if not parts:
            return ChannelLocator(custom=u)

        # /channel/UCxxxx
        if len(parts) >= 2 and parts[0].lower() == "channel":
            return ChannelLocator(channel_id=parts[1])

        # /@handle
        if parts[0].startswith("@"):
            return ChannelLocator(handle=parts[0][1:])

        # /user/username
        if len(parts) >= 2 and parts[0].lower() == "user":
            return ChannelLocator(username=parts[1])

        # /c/customName (not directly resolvable -> search)
        if len(parts) >= 2 and parts[0].lower() == "c":
            return ChannelLocator(custom=parts[1])

        # Sometimes people paste only "@handle" or "UC..." etc.
        if parts and _YT_CHANNEL_ID_RE.match(parts[0]):
            return ChannelLocator(channel_id=parts[0])
        if parts and parts[0].startswith("@"):
            return ChannelLocator(handle=parts[0][1:])

        return ChannelLocator(custom=parts[0])

    return ChannelLocator()


async def _yt_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    api_key = _require_youtube_key()

    full_url = f"{settings.youtube_api_base.rstrip('/')}/{path.lstrip('/')}"
    merged = {**params, "key": api_key}

    cache_key = f"{full_url}?" + "&".join(f"{k}={merged[k]}" for k in sorted(merged.keys()))
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {"User-Agent": settings.http_user_agent}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.get(full_url, params=merged, headers=headers)

    if resp.status_code >= 400:
        raise httpx.HTTPStatusError("YouTube API error", request=resp.request, response=resp)

    data = resp.json()
    _cache[cache_key] = data
    return data


async def resolve_channel(locator: ChannelLocator) -> dict[str, Any]:
    # Returns the first channel item from channels.list
    params: dict[str, Any] = {
        "part": "snippet,statistics,brandingSettings",
        "maxResults": 1,
    }

    if locator.channel_id:
        params["id"] = locator.channel_id
    elif locator.handle:
        params["forHandle"] = locator.handle
    elif locator.username:
        params["forUsername"] = locator.username
    elif locator.custom:
        # Use search to find a channel, then fetch channels.list by id
        search = await _yt_get(
            "search",
            {
                "part": "snippet",
                "type": "channel",
                "q": locator.custom,
                "maxResults": 1,
            },
        )
        items = search.get("items") or []
        if not items:
            return {}
        channel_id = ((items[0] or {}).get("id") or {}).get("channelId")
        if not channel_id:
            return {}
        params["id"] = channel_id
    else:
        return {}

    data = await _yt_get("channels", params)
    items = data.get("items") or []
    return items[0] if items else {}


async def latest_videos(*, channel_id: str, max_results: int) -> list[dict[str, Any]]:
    # search.list gets latest video IDs; videos.list gets stats.
    search = await _yt_get(
        "search",
        {
            "part": "snippet",
            "channelId": channel_id,
            "order": "date",
            "type": "video",
            "maxResults": max_results,
        },
    )

    items = search.get("items") or []
    video_ids = [(((it or {}).get("id") or {}).get("videoId")) for it in items]
    video_ids = [vid for vid in video_ids if vid]
    if not video_ids:
        return []

    videos = await _yt_get(
        "videos",
        {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(video_ids),
            "maxResults": len(video_ids),
        },
    )

    # Keep original order
    by_id = {v.get("id"): v for v in (videos.get("items") or []) if v.get("id")}
    return [by_id[vid] for vid in video_ids if vid in by_id]


async def video_details(*, video_id: str) -> dict[str, Any]:
    data = await _yt_get(
        "videos",
        {
            "part": "snippet,statistics,contentDetails",
            "id": video_id,
            "maxResults": 1,
        },
    )
    items = data.get("items") or []
    return items[0] if items else {}


async def search(*, q: str, kind: str, max_results: int) -> list[dict[str, Any]]:
    if kind not in {"video", "channel"}:
        raise ValueError("kind must be video or channel")
    data = await _yt_get(
        "search",
        {
            "part": "snippet",
            "q": q,
            "type": kind,
            "maxResults": max_results,
            "safeSearch": "none",
        },
    )
    return data.get("items") or []


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
