"""High-level steps shared by the CLI and the web app."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .config import Settings
from .parser import Track, parse_tracklist
from .spotify_client import MatchResult, SpotifyService
from .youtube import VideoInfo, extract_video_id, fetch_comments, fetch_video

log = logging.getLogger(__name__)

MIN_TRACKS = 3


@dataclass
class TracklistResult:
    video: VideoInfo
    tracks: list[Track]
    source: str  # description | chapters | comments | manual

    @property
    def set_artist(self) -> str | None:
        return guess_set_artist(self.video.title)

    def to_dict(self) -> dict:
        return {
            "video": self.video.to_dict(),
            "tracks": [t.to_dict() for t in self.tracks],
            "source": self.source,
            "set_artist": self.set_artist,
        }


_SET_TITLE_SPLIT_RE = re.compile(
    r"\s+[-–—|@]\s+|\s*[|@]\s*|\s+(?:live|dj\s+set|set|at|in|for|b2b|presents)\b|\s*[(\[]", re.I
)
_NOT_AN_ARTIST_RE = re.compile(r"\b(?:boiler\s*room|cercle|mix|set|live|podcast|essential|radio|episode|ep\.?)\b", re.I)


def guess_set_artist(video_title: str) -> str | None:
    """'Ninajirachi - live NYC @ ...' -> 'Ninajirachi'. None if it doesn't look like a name."""
    head = _SET_TITLE_SPLIT_RE.split(video_title or "", maxsplit=1)[0].strip(" -–—|:")
    if not head or len(head) > 40 or _NOT_AN_ARTIST_RE.search(head):
        return None
    return head


def load_tracklist(
    url: str | None,
    settings: Settings,
    *,
    manual_text: str | None = None,
    check_comments: bool = True,
) -> TracklistResult:
    """Fetch the video and find the best tracklist for it.

    Order: pasted text > description > yt-dlp chapters > top comments.
    """
    if manual_text and manual_text.strip():
        video = fetch_video(url, settings.youtube_api_key) if url else VideoInfo("", "DJ Set", "", source="manual")
        return TracklistResult(video, parse_tracklist(manual_text, loose=True), "manual")

    if not url:
        raise ValueError("A YouTube URL or a pasted tracklist is required.")

    video = fetch_video(url, settings.youtube_api_key)
    tracks = parse_tracklist(video.description)
    source = "description"

    if len(tracks) < MIN_TRACKS and video.chapters:
        chapter_text = "\n".join(f"{_fmt_seconds(c['start'])} {c['title']}" for c in video.chapters)
        chapter_tracks = parse_tracklist(chapter_text)
        if len(chapter_tracks) > len(tracks):
            tracks, source = chapter_tracks, "chapters"

    if len(tracks) < MIN_TRACKS and check_comments:
        log.info("Description has no usable tracklist; checking top comments")
        best: list[Track] = []
        for comment in fetch_comments(video.video_id, settings.youtube_api_key):
            parsed = parse_tracklist(comment)
            if len(parsed) > len(best):
                best = parsed
        if len(best) > len(tracks):
            tracks, source = best, "comments"

    return TracklistResult(video, tracks, source)


def match_tracks(
    service: SpotifyService,
    tracks: Iterable[Track],
    on_result: Callable[[MatchResult], None] | None = None,
    context_artist: str | None = None,
) -> list[MatchResult]:
    results = []
    for track in tracks:
        try:
            result = service.match(track, context_artist)
        except Exception as exc:  # one bad search shouldn't kill the run
            log.warning("Search failed for %s: %s", track.display, exc)
            result = MatchResult(track=track)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def build_playlist(
    service: SpotifyService,
    name: str,
    uris: list[str],
    *,
    public: bool = False,
    description: str = "",
) -> dict:
    playlist = service.create_playlist(name, public=public, description=description)
    added = service.add_tracks(playlist["id"], uris) if uris else 0
    return {
        "id": playlist["id"],
        "name": playlist.get("name", name),
        "url": (playlist.get("external_urls") or {}).get("spotify"),
        "added": added,
    }


def default_description(video: VideoInfo) -> str:
    parts = ["Tracklist from"]
    parts.append(f"{video.title} by {video.channel}" if video.channel else video.title or "a DJ set")
    if video.url:
        parts.append(f"- {video.url}")
    return " ".join(parts)


def write_unmatched_report(
    results: Iterable[MatchResult],
    video: VideoInfo,
    out_dir: str | Path,
    *,
    path: str | Path | None = None,
) -> Path | None:
    missing = [r for r in results if not r.matched]
    if not missing:
        return None
    if path is None:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", video.title or video.video_id or "set").strip("-")[:60] or "set"
        path = Path(out_dir) / f"unmatched_{slug}_{datetime.now():%Y%m%d-%H%M%S}.txt"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"Unmatched tracks for: {video.title}",
        f"Video: {video.url}" if video.url else "",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
    ]
    for r in missing:
        t = r.track
        stamp = f"[{t.timestamp}] " if t.timestamp else ""
        note = f"  (closest: {', '.join(r.artists)} - {r.name}, score {r.score:.2f})" if r.status == "uncertain" else ""
        lines.append(f"{stamp}{t.display}{note}")
        if t.raw and t.raw != t.display:
            lines.append(f"    raw: {t.raw}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _fmt_seconds(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


__all__ = [
    "TracklistResult",
    "build_playlist",
    "default_description",
    "extract_video_id",
    "load_tracklist",
    "match_tracks",
    "write_unmatched_report",
]
