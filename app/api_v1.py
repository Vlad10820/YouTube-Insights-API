from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl

from .errors import http_error
from .settings import settings
from .limiting import limiter
from .youtube import (
    YouTubeConfigError,
    latest_videos,
    now_utc,
    parse_youtube_video_id,
    parse_youtube_channel_input,
    resolve_channel,
    search,
    video_details,
)


router = APIRouter()

_MAX_BYTES = 1_000_000  # 1MB


class MetaResponse(BaseModel):
    ok: bool
    fetched_at: datetime
    requested_url: str
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None

    title: str | None = None
    description: str | None = None
    site_name: str | None = None
    image: str | None = None
    favicon: str | None = None


class ErrorInfo(BaseModel):
    code: str = Field(examples=["validation_error", "not_found", "upstream_error", "server_misconfigured"])
    message: str = Field(examples=["Something went wrong"])


class ErrorResponse(BaseModel):
    error: ErrorInfo
    request_id: str | None = Field(default=None, examples=["b4c6c0a0-0a2f-4b79-9f5c-2e2a8f6a4b1d"])


class YouTubeChannelStats(BaseModel):
    ok: bool
    fetched_at: datetime

    channel_id: str
    title: str | None = None
    description: str | None = None
    custom_url: str | None = None
    published_at: str | None = None
    thumbnail_url: str | None = None

    view_count: int | None = None
    subscriber_count: int | None = None
    video_count: int | None = None


class YouTubeVideoItem(BaseModel):
    video_id: str
    title: str | None = None
    published_at: str | None = None
    thumbnail_url: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None


class YouTubeLatestVideosResponse(BaseModel):
    ok: bool
    fetched_at: datetime
    channel_id: str
    videos: list[YouTubeVideoItem]


class YouTubeVideoStats(BaseModel):
    ok: bool
    fetched_at: datetime
    video_id: str

    channel_id: str | None = None
    channel_title: str | None = None
    title: str | None = None
    description: str | None = None
    published_at: str | None = None
    duration: str | None = None
    thumbnail_url: str | None = None

    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None


class YouTubeSearchItem(BaseModel):
    kind: str
    id: str
    title: str | None = None
    description: str | None = None
    published_at: str | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    thumbnail_url: str | None = None


class YouTubeSearchResponse(BaseModel):
    ok: bool
    fetched_at: datetime
    q: str
    type: str
    results: list[YouTubeSearchItem]


def _to_int(value: Any) -> int | None:
    """YouTube returns counts as strings and hides some of them; turn them into ints or None."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first(*values: str | None) -> str | None:
    for value in values:
        if value:
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def _make_absolute(base_url: str, maybe_url: str | None) -> str | None:
    if not maybe_url:
        return None
    try:
        return urljoin(base_url, maybe_url)
    except Exception:
        return maybe_url


def _extract_meta(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    def meta(property_name: str | None = None, name: str | None = None) -> str | None:
        if property_name:
            tag = soup.find("meta", attrs={"property": property_name})
            if tag and tag.get("content"):
                return str(tag.get("content"))
        if name:
            tag = soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return str(tag.get("content"))
        return None

    title_tag = soup.find("title")
    title = _first(
        meta(property_name="og:title"),
        meta(name="twitter:title"),
        str(title_tag.text) if title_tag else None,
    )

    description = _first(
        meta(property_name="og:description"),
        meta(name="twitter:description"),
        meta(name="description"),
    )

    site_name = _first(meta(property_name="og:site_name"))

    image = _first(
        meta(property_name="og:image"),
        meta(name="twitter:image"),
        meta(name="twitter:image:src"),
    )

    favicon = None
    icon_tag = soup.find("link", rel=lambda v: v and "icon" in str(v).lower())
    if icon_tag and icon_tag.get("href"):
        favicon = str(icon_tag.get("href"))

    return {
        "title": title,
        "description": description,
        "site_name": site_name,
        "image": _make_absolute(base_url, image),
        "favicon": _make_absolute(base_url, favicon),
    }


@router.get(
    "/whoami",
    tags=["System"],
    summary="Show auth + default limits",
    responses={401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
@limiter.limit("120/minute")
def whoami(request: Request):
    return {
        "ok": True,
        "require_api_key": settings.require_api_key,
        "rate_limit_default": settings.rate_limit_default,
    }


@router.get(
    "/meta",
    response_model=MetaResponse,
    tags=["Metadata"],
    summary="Extract page metadata (OG/Twitter/title)",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
@limiter.limit("30/minute")
async def meta(request: Request, url: HttpUrl):
    parsed = urlparse(str(url))
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")

    headers = {
        "User-Agent": settings.http_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    fetched_at = datetime.now(timezone.utc)

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
            resp = await client.get(str(url), headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc.__class__.__name__}") from exc

    content_type = resp.headers.get("content-type")
    if content_type and "text/html" not in content_type and "application/xhtml" not in content_type:
        return MetaResponse(
            ok=False,
            fetched_at=fetched_at,
            requested_url=str(url),
            final_url=str(resp.url),
            status_code=resp.status_code,
            content_type=content_type,
        )

    raw = resp.content[:_MAX_BYTES]
    encoding = resp.encoding or "utf-8"
    html = raw.decode(encoding, errors="replace")

    base_url = str(resp.url)
    meta_fields = _extract_meta(html=html, base_url=base_url)

    return MetaResponse(
        ok=True,
        fetched_at=fetched_at,
        requested_url=str(url),
        final_url=base_url,
        status_code=resp.status_code,
        content_type=content_type,
        **meta_fields,
    )


@router.get(
    "/youtube/channel",
    response_model=YouTubeChannelStats,
    tags=["YouTube"],
    summary="Get YouTube channel stats",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
@limiter.limit("30/minute")
async def youtube_channel(
    request: Request,
    channel_id: str | None = None,
    handle: str | None = None,
    username: str | None = None,
    url: str | None = None,
):
    locator = parse_youtube_channel_input(
        channel_id=channel_id,
        handle=handle,
        username=username,
        url=url,
    )
    if not any([locator.channel_id, locator.handle, locator.username, locator.custom]):
        raise http_error(
            status_code=422,
            code="validation_error",
            message="Provide one of: channel_id, handle, username, or url",
            request_id=getattr(request.state, "request_id", None),
        )

    fetched_at = now_utc()
    try:
        channel = await resolve_channel(locator)
    except YouTubeConfigError as exc:
        raise http_error(
            status_code=500,
            code="server_misconfigured",
            message=str(exc),
            request_id=getattr(request.state, "request_id", None),
        ) from exc
    except httpx.HTTPError as exc:
        raise http_error(
            status_code=502,
            code="upstream_error",
            message="YouTube API request failed",
            request_id=getattr(request.state, "request_id", None),
        ) from exc

    if not channel:
        raise http_error(
            status_code=404,
            code="not_found",
            message="Channel not found",
            request_id=getattr(request.state, "request_id", None),
        )

    snippet = channel.get("snippet") or {}
    stats = channel.get("statistics") or {}
    thumbs = (snippet.get("thumbnails") or {}).get("high") or (snippet.get("thumbnails") or {}).get("default") or {}

    return YouTubeChannelStats(
        ok=True,
        fetched_at=fetched_at,
        channel_id=str(channel.get("id")),
        title=snippet.get("title"),
        description=snippet.get("description"),
        custom_url=snippet.get("customUrl"),
        published_at=snippet.get("publishedAt"),
        thumbnail_url=thumbs.get("url"),
        view_count=_to_int(stats.get("viewCount")),
        subscriber_count=_to_int(stats.get("subscriberCount")),
        video_count=_to_int(stats.get("videoCount")),
    )


@router.get(
    "/youtube/latest",
    response_model=YouTubeLatestVideosResponse,
    tags=["YouTube"],
    summary="Get latest videos for a channel",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
@limiter.limit("30/minute")
async def youtube_latest(
    request: Request,
    channel_id: str,
    max_results: int = 10,
):
    if max_results < 1 or max_results > 25:
        raise http_error(
            status_code=422,
            code="validation_error",
            message="max_results must be between 1 and 25",
            request_id=getattr(request.state, "request_id", None),
        )

    fetched_at = now_utc()
    try:
        videos = await latest_videos(channel_id=channel_id, max_results=max_results)
    except YouTubeConfigError as exc:
        raise http_error(
            status_code=500,
            code="server_misconfigured",
            message=str(exc),
            request_id=getattr(request.state, "request_id", None),
        ) from exc
    except httpx.HTTPError as exc:
        raise http_error(
            status_code=502,
            code="upstream_error",
            message="YouTube API request failed",
            request_id=getattr(request.state, "request_id", None),
        ) from exc

    out: list[YouTubeVideoItem] = []
    for v in videos:
        vid = v.get("id")
        snippet = v.get("snippet") or {}
        stats = v.get("statistics") or {}
        thumbs = (snippet.get("thumbnails") or {}).get("high") or (snippet.get("thumbnails") or {}).get("default") or {}

        out.append(
            YouTubeVideoItem(
                video_id=str(vid),
                title=snippet.get("title"),
                published_at=snippet.get("publishedAt"),
                thumbnail_url=thumbs.get("url"),
                view_count=_to_int(stats.get("viewCount")),
                like_count=_to_int(stats.get("likeCount")),
                comment_count=_to_int(stats.get("commentCount")),
            )
        )

    return YouTubeLatestVideosResponse(
        ok=True,
        fetched_at=fetched_at,
        channel_id=channel_id,
        videos=out,
    )


@router.get(
    "/youtube/video",
    response_model=YouTubeVideoStats,
    tags=["YouTube"],
    summary="Get video stats by ID or URL",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
@limiter.limit("30/minute")
async def youtube_video(
    request: Request,
    video_id: str | None = None,
    url: str | None = None,
):
    vid = parse_youtube_video_id(video_id=video_id, url=url)
    if not vid:
        raise http_error(
            status_code=422,
            code="validation_error",
            message="Provide a valid 11-character video_id or a YouTube video URL",
            request_id=getattr(request.state, "request_id", None),
        )

    fetched_at = now_utc()
    try:
        v = await video_details(video_id=vid)
    except YouTubeConfigError as exc:
        raise http_error(
            status_code=500,
            code="server_misconfigured",
            message=str(exc),
            request_id=getattr(request.state, "request_id", None),
        ) from exc
    except httpx.HTTPError as exc:
        raise http_error(
            status_code=502,
            code="upstream_error",
            message="YouTube API request failed",
            request_id=getattr(request.state, "request_id", None),
        ) from exc

    if not v:
        raise http_error(
            status_code=404,
            code="not_found",
            message="Video not found",
            request_id=getattr(request.state, "request_id", None),
        )

    snippet = v.get("snippet") or {}
    stats = v.get("statistics") or {}
    content = v.get("contentDetails") or {}
    thumbs = (snippet.get("thumbnails") or {}).get("high") or (snippet.get("thumbnails") or {}).get("default") or {}

    return YouTubeVideoStats(
        ok=True,
        fetched_at=fetched_at,
        video_id=str(v.get("id")),
        channel_id=snippet.get("channelId"),
        channel_title=snippet.get("channelTitle"),
        title=snippet.get("title"),
        description=snippet.get("description"),
        published_at=snippet.get("publishedAt"),
        duration=content.get("duration"),
        thumbnail_url=thumbs.get("url"),
        view_count=_to_int(stats.get("viewCount")),
        like_count=_to_int(stats.get("likeCount")),
        comment_count=_to_int(stats.get("commentCount")),
    )


@router.get(
    "/youtube/search",
    response_model=YouTubeSearchResponse,
    tags=["YouTube"],
    summary="Search YouTube for videos or channels",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
@limiter.limit("30/minute")
async def youtube_search(
    request: Request,
    q: str,
    type: str = "video",
    max_results: int = 10,
):
    if max_results < 1 or max_results > 25:
        raise http_error(
            status_code=422,
            code="validation_error",
            message="max_results must be between 1 and 25",
            request_id=getattr(request.state, "request_id", None),
        )
    if type not in {"video", "channel"}:
        raise http_error(
            status_code=422,
            code="validation_error",
            message="type must be video or channel",
            request_id=getattr(request.state, "request_id", None),
        )

    fetched_at = now_utc()
    try:
        items = await search(q=q, kind=type, max_results=max_results)
    except YouTubeConfigError as exc:
        raise http_error(
            status_code=500,
            code="server_misconfigured",
            message=str(exc),
            request_id=getattr(request.state, "request_id", None),
        ) from exc
    except (ValueError,) as exc:
        raise http_error(
            status_code=422,
            code="validation_error",
            message=str(exc),
            request_id=getattr(request.state, "request_id", None),
        ) from exc
    except httpx.HTTPError as exc:
        raise http_error(
            status_code=502,
            code="upstream_error",
            message="YouTube API request failed",
            request_id=getattr(request.state, "request_id", None),
        ) from exc

    results: list[YouTubeSearchItem] = []
    for it in items:
        kind = (((it or {}).get("id") or {}).get("kind")) or ""
        id_obj = (it or {}).get("id") or {}
        vid = id_obj.get("videoId")
        cid = id_obj.get("channelId")
        item_id = vid or cid or ""
        snippet = (it or {}).get("snippet") or {}
        thumbs = (snippet.get("thumbnails") or {}).get("high") or (snippet.get("thumbnails") or {}).get("default") or {}

        results.append(
            YouTubeSearchItem(
                kind=kind,
                id=item_id,
                title=snippet.get("title"),
                description=snippet.get("description"),
                published_at=snippet.get("publishedAt"),
                channel_id=snippet.get("channelId"),
                channel_title=snippet.get("channelTitle"),
                thumbnail_url=thumbs.get("url"),
            )
        )

    return YouTubeSearchResponse(
        ok=True,
        fetched_at=fetched_at,
        q=q,
        type=type,
        results=results,
    )
