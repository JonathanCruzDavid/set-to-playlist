"""Spotify integration: OAuth, smart track search, playlist creation."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import spotipy
from spotipy.cache_handler import CacheFileHandler, CacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from .config import Settings
from .parser import REMIX_RE, Track

log = logging.getLogger(__name__)

# user-read-private: needed for market="from_token" (search in the user's own country catalog)
SCOPES = "playlist-modify-public playlist-modify-private user-read-private"
SEARCH_LIMIT = 10           # Spotify's dev-mode cap; also plenty for ranking
MATCH_THRESHOLD = 0.62      # artist + title known
TITLE_ONLY_THRESHOLD = 0.80  # only a title was parsed
UNCERTAIN_THRESHOLD = 0.45  # below this a candidate isn't worth suggesting
ADD_BATCH = 100


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


def build_oauth(
    settings: Settings,
    *,
    cache_handler: CacheHandler | None = None,
    state: str | None = None,
    open_browser: bool = True,
) -> SpotifyOAuth:
    settings.require_spotify()
    return SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=SCOPES,
        cache_handler=cache_handler or CacheFileHandler(cache_path=settings.spotify_cache_path),
        state=state,
        open_browser=open_browser,
    )


def build_client(auth_manager: SpotifyOAuth) -> spotipy.Spotify:
    # spotipy retries 429/5xx itself, honouring Retry-After.
    return spotipy.Spotify(
        auth_manager=auth_manager,
        requests_timeout=15,
        retries=3,
        status_retries=3,
        backoff_factor=0.5,
    )


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


@dataclass
class MatchResult:
    track: Track
    status: str = "unmatched"  # matched | uncertain | unmatched
    score: float = 0.0
    query: str | None = None
    uri: str | None = None
    id: str | None = None
    name: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    image: str | None = None
    url: str | None = None
    queries_tried: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.status == "matched"

    def to_dict(self) -> dict:
        return {
            "track": self.track.to_dict(),
            "status": self.status,
            "score": round(self.score, 3),
            "query": self.query,
            "uri": self.uri,
            "id": self.id,
            "name": self.name,
            "artists": self.artists,
            "album": self.album,
            "image": self.image,
            "url": self.url,
            "queries_tried": self.queries_tried,
        }


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ").replace("$", "s")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # Containment: "glue" vs "glue 2024 remaster"
    if f" {a} " in f" {b} " or f" {b} " in f" {a} ":
        ratio = max(ratio, 0.88)
    return ratio


def _candidate_base(name: str) -> str:
    name = re.sub(r"\s+-\s+.*$", "", name)  # "Title - Fisher Remix"
    name = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", name)
    return name.strip()


def score_candidate(track: Track, item: dict) -> float:
    cand_name = item.get("name") or ""
    cand_artists = [a.get("name", "") for a in item.get("artists") or []]

    n_cand = normalize(cand_name)
    title_score = max(
        _similar(normalize(track.query_title), n_cand),
        _similar(normalize(track.base_title), normalize(_candidate_base(cand_name))),
    )

    # Prefer the right version: remix wanted -> remix found, and vice versa.
    if track.remix:
        remix_words = [w for w in normalize(track.remix).split() if w not in ("remix", "edit", "mix", "rmx")]
        if remix_words and all(w in n_cand.split() or w in normalize(" ".join(cand_artists)) for w in remix_words):
            title_score = min(1.0, title_score + 0.1)
        else:
            title_score -= 0.08
    elif REMIX_RE.search(re.sub(r"(?i)original|extended|radio|remaster(ed)?", "", cand_name)):
        title_score -= 0.05

    if not track.artist:
        return max(0.0, title_score)

    expected = [normalize(a) for a in track.artists + track.featured] or [normalize(track.artist)]
    cand_norm = [normalize(a) for a in cand_artists]
    artist_score = max((_similar(e, c) for e in expected for c in cand_norm), default=0.0)
    joined = " " + " ".join(cand_norm) + " "
    if any(e and f" {e} " in joined for e in expected):
        artist_score = max(artist_score, 0.92)

    return max(0.0, 0.55 * title_score + 0.45 * artist_score)


def build_queries(track: Track) -> list[str]:
    """Search attempts, most specific first.

    1. full free-text (keeps remix credit)
    2. field-filtered artist + base title (remix credit stripped)
    3. free-text primary artist + base title
    4. base title only (scored with a stricter threshold)
    """
    artist = track.primary_artist
    full_artist = " ".join(re.sub(r"\([^()]*\)", " ", track.artist).split())  # drop "(Keinemusik)"
    queries: list[str] = []
    if artist:
        queries.append(f"{full_artist} {track.query_title}")
        queries.append(f"track:{track.base_title} artist:{artist}")
        queries.append(f"{artist} {track.base_title}")
    queries.append(track.query_title)
    if track.base_title != track.query_title:
        queries.append(track.base_title)

    seen, out = set(), []
    for q in queries:
        q = re.sub(r"[\"“”]", "", " ".join(q.split()))[:250]
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class SpotifyService:
    def __init__(self, sp: spotipy.Spotify):
        self.sp = sp
        self._legacy_endpoints = False
        self._market: str | None = "from_token"

    @classmethod
    def for_cli(cls, settings: Settings) -> "SpotifyService":
        """File-cached token; opens a browser on first run."""
        return cls(build_client(build_oauth(settings, open_browser=True)))

    def me(self) -> dict:
        return self.sp.me()

    def search(self, query: str) -> list[dict]:
        try:
            resp = self.sp.search(q=query, type="track", limit=SEARCH_LIMIT, market=self._market)
        except SpotifyException as exc:
            if exc.http_status != 403 or self._market is None:
                raise
            # Token lacks the scope for market=from_token; search the global catalog instead.
            log.debug("Search with market=from_token refused (%s); retrying without market", exc.msg)
            self._market = None
            resp = self.sp.search(q=query, type="track", limit=SEARCH_LIMIT)
        return [i for i in (resp.get("tracks") or {}).get("items") or [] if i and i.get("uri")]

    def match(self, track: Track, context_artist: str | None = None) -> MatchResult:
        """Find ``track`` on Spotify.

        ``context_artist`` (usually the DJ, from the video title) is tried first for
        lines that only name a title, since sets often list the DJ's own tracks that way.
        """
        if not track.artist and context_artist:
            hinted = self.match(Track(track.index, context_artist, track.title, track.timestamp, track.seconds, track.raw))
            if hinted.matched:
                hinted.track = track
                return hinted
        result = MatchResult(track=track)
        threshold = MATCH_THRESHOLD if track.artist else TITLE_ONLY_THRESHOLD
        best_item, best_score, best_query = None, -1.0, None

        for query in build_queries(track):
            result.queries_tried.append(query)
            items = self.search(query)
            if not items:
                log.debug("No results for %r, falling back", query)
                continue
            for item in items:
                score = score_candidate(track, item)
                if score > best_score:
                    best_item, best_score, best_query = item, score, query
            if best_score >= threshold:
                break

        if best_item is None:
            return result

        result.score = best_score
        result.query = best_query
        if best_score >= threshold:
            result.status = "matched"
        elif best_score >= UNCERTAIN_THRESHOLD:
            result.status = "uncertain"
        else:
            return result

        images = (best_item.get("album") or {}).get("images") or []
        result.uri = best_item["uri"]
        result.id = best_item.get("id")
        result.name = best_item.get("name")
        result.artists = [a.get("name", "") for a in best_item.get("artists") or []]
        result.album = (best_item.get("album") or {}).get("name")
        result.image = images[-1]["url"] if images else None
        result.url = (best_item.get("external_urls") or {}).get("spotify")
        return result

    # ------------------------------------------------------------------ #
    # Playlists. Spotify moved these to /me/playlists and /playlists/{id}/items;
    # we try those first and fall back to the older endpoints if needed.
    # ------------------------------------------------------------------ #

    def create_playlist(self, name: str, *, public: bool = False, description: str = "") -> dict:
        payload = {
            "name": " ".join(name.split())[:100] or "DJ Set",
            "public": bool(public),
            "description": " ".join(description.split())[:300],
        }
        if not self._legacy_endpoints:
            try:
                return self.sp._post("me/playlists", payload=payload)
            except SpotifyException as exc:
                if exc.http_status not in (404, 405):
                    raise
                log.debug("POST me/playlists unavailable, using legacy endpoint")
                self._legacy_endpoints = True
        user_id = self.me()["id"]
        return self.sp.user_playlist_create(
            user_id, payload["name"], public=payload["public"], description=payload["description"]
        )

    def add_tracks(self, playlist_id: str, uris: list[str]) -> int:
        uris = list(dict.fromkeys(u for u in uris if u))  # dedupe, keep order
        for i in range(0, len(uris), ADD_BATCH):
            batch = uris[i : i + ADD_BATCH]
            if not self._legacy_endpoints:
                try:
                    self.sp._post(f"playlists/{playlist_id}/items", payload={"uris": batch})
                    continue
                except SpotifyException as exc:
                    if exc.http_status not in (404, 405):
                        raise
                    self._legacy_endpoints = True
            self.sp.playlist_add_items(playlist_id, batch)
        return len(uris)
