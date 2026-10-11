"""YouTube metadata fetching.

Uses the official YouTube Data API v3 when ``YOUTUBE_API_KEY`` is set and
falls back to yt-dlp (no key required) otherwise or if the API call fails.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YouTubeError(RuntimeError):
    """Video could not be resolved or fetched."""


@dataclass
class VideoInfo:
    video_id: str
    title: str
    description: str
    channel: str = ""
    thumbnail: str | None = None
    source: str = ""  # "youtube-api" | "yt-dlp" | "manual"
    chapters: list[dict] = field(default_factory=list)  # [{"start": float, "title": str}]

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}" if self.video_id else ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["url"] = self.url
        return data


def extract_video_id(url: str) -> str:
    """Return the 11-char video ID from any common YouTube URL form."""
    url = (url or "").strip()
    if _ID_RE.match(url):
        return url
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]

    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        query = parse_qs(parsed.query)
        if query.get("v"):
            candidate = query["v"][0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v", "e"):
                candidate = parts[1]
    else:
        raise YouTubeError(f"Not a YouTube URL: {url}")

    if not _ID_RE.match(candidate):
        raise YouTubeError(f"Could not find a video ID in: {url}")
    return candidate


def fetch_video(url: str, api_key: str | None = None) -> VideoInfo:
    video_id = extract_video_id(url)
    if api_key:
        try:
            return _fetch_with_api(video_id, api_key)
        except YouTubeError:
            raise
        except Exception as exc:  # network / quota / bad key
            log.warning("YouTube Data API failed (%s); falling back to yt-dlp", exc)
    return _fetch_with_ytdlp(video_id)


def fetch_comments(video_id: str, api_key: str | None = None, limit: int = 50) -> list[str]:
    """Top-level comments, most relevant first. Tracklists are often pinned there."""
    if api_key:
        try:
            return _api_comments(video_id, api_key, limit)
        except Exception as exc:
            log.warning("Could not fetch comments via API (%s); trying yt-dlp", exc)
    try:
        return _ytdlp_comments(video_id, limit)
    except Exception as exc:
        log.warning("Could not fetch comments via yt-dlp: %s", exc)
        return []


# --------------------------------------------------------------------------- #
# YouTube Data API v3
# --------------------------------------------------------------------------- #


def _youtube_client(api_key: str):
    from googleapiclient.discovery import build

    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def _fetch_with_api(video_id: str, api_key: str) -> VideoInfo:
    resp = _youtube_client(api_key).videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items") or []
    if not items:
        raise YouTubeError(f"Video {video_id} not found (it may be private or deleted).")
    snippet = items[0]["snippet"]
    thumbs = snippet.get("thumbnails") or {}
    thumb = next((thumbs[k]["url"] for k in ("maxres", "high", "medium", "default") if k in thumbs), None)
    return VideoInfo(
        video_id=video_id,
        title=snippet.get("title", ""),
        description=snippet.get("description", ""),
        channel=snippet.get("channelTitle", ""),
        thumbnail=thumb,
        source="youtube-api",
    )


def _api_comments(video_id: str, api_key: str, limit: int) -> list[str]:
    from googleapiclient.errors import HttpError

    try:
        resp = (
            _youtube_client(api_key)
            .commentThreads()
            .list(
                part="snippet",
                videoId=video_id,
                maxResults=min(limit, 100),
                order="relevance",
                textFormat="plainText",
            )
            .execute()
        )
    except HttpError as exc:
        if getattr(exc, "status_code", None) == 403 or "commentsDisabled" in str(exc):
            return []
        raise
    out = []
    for item in resp.get("items", []):
        top = item["snippet"]["topLevelComment"]["snippet"]
        out.append(top.get("textOriginal") or top.get("textDisplay") or "")
    return out


# --------------------------------------------------------------------------- #
# yt-dlp fallback
# --------------------------------------------------------------------------- #


def _ytdlp_extract(video_id: str, extra_opts: dict | None = None) -> dict:
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        **(extra_opts or {}),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise YouTubeError(f"yt-dlp could not fetch {video_id}: {exc}") from exc


def _fetch_with_ytdlp(video_id: str) -> VideoInfo:
    info = _ytdlp_extract(video_id)
    chapters = [
        {"start": float(c.get("start_time") or 0), "title": c.get("title") or ""}
        for c in (info.get("chapters") or [])
    ]
    return VideoInfo(
        video_id=video_id,
        title=info.get("title") or "",
        description=info.get("description") or "",
        channel=info.get("uploader") or info.get("channel") or "",
        thumbnail=info.get("thumbnail"),
        source="yt-dlp",
        chapters=chapters,
    )


def _ytdlp_comments(video_id: str, limit: int) -> list[str]:
    info = _ytdlp_extract(
        video_id,
        {
            "getcomments": True,
            "extractor_args": {"youtube": {"max_comments": [str(limit), str(limit), "0", "0"], "comment_sort": ["top"]}},
        },
    )
    return [c.get("text", "") for c in (info.get("comments") or []) if c.get("parent", "root") == "root"]
