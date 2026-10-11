"""Tracklist parsing.

Turns free-form YouTube descriptions like::

    00:00 Intro
    01:23 Bicep - Glue (Original Mix) [Ninja Tune]
    1:02:03 | Fred again.. & Swedish House Mafia – Turn On The Lights again.. (Extended Mix)
    12. Peggy Gou - (It Goes Like) Nanana [XL] 58:10

into clean ``Track`` objects with separate artist / title fields, stripping
URLs, handles, emoji, labels and redundant version tags while keeping remix
credits (they matter for finding the right version on Spotify).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #

_TS = r"(?:\d{1,2}:)?\d{1,2}:\d{2}"
_NUMBERING = r"(?:\#?\d{1,3}[.)]\s*)?"

# "01:23 Artist - Title", "[1:02:03] - Artist - Title", "3. 05:00 | Artist - Title"
LEADING_TS_RE = re.compile(
    rf"^\s*{_NUMBERING}[\[\(]?(?P<ts>{_TS})[\]\)]?(?:\s*[-–—|:.)>~]+\s*|\s+)(?P<rest>.+?)\s*$"
)
# "Artist - Title 01:23", "Artist - Title (1:02:03)"
TRAILING_TS_RE = re.compile(
    rf"^\s*{_NUMBERING}(?P<rest>.*?[^\W\d_].*?)[\s\-–—|@]*[\[\(]?(?<![\d:])(?P<ts>{_TS})[\]\)]?\s*$"
)
# "12. Artist - Title", "#3 Artist - Title", "04 - Artist - Title"
NUMBERED_RE = re.compile(r"^\s*(?:\#|track\s*)?(?P<n>\d{1,3})\s*[.):\-–—]\s*(?P<rest>.+?)\s*$", re.I)

_ANY_TS_RE = re.compile(rf"(?<![\d:])[\[\(]?{_TS}[\]\)]?(?![\d:])")
_LEADING_TS_STRIP_RE = re.compile(rf"^\s*[\[\(]?{_TS}[\]\)]?\s*[-–—|:.)>~]*\s*")

URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
HANDLE_RE = re.compile(r"(?<![\w.])[@#][\w.]+")
BRACKET_RE = re.compile(r"([\(\[\{])\s*([^\(\)\[\]\{\}]*?)\s*[\)\]\}]")

# Version tags that add nothing to a search (dropped entirely).
NOISE_TAG_RE = re.compile(
    r"^(?:"
    r"original(?:\s+(?:mix|version|edit))?|extended(?:\s+(?:mix|version|edit|club\s+mix))?|"
    r"radio\s+(?:edit|mix|version)|club\s+(?:mix|edit|version)|album\s+(?:version|edit|mix)|"
    r"single\s+(?:version|edit)|main\s+mix|full\s+(?:version|mix)|clean|explicit|dirty|"
    r"(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?(?:\s+version)?|"
    r"official\s+(?:video|audio|music\s+video)|lyrics?|hq|hd|mixed|mastered|clip|preview|snippet|"
    r"id|id\s+(?:remix|edit|mix|rework|bootleg|flip)|unreleased|forthcoming|premiere|white\s+label"
    r")$",
    re.I,
)
# Promotional phrases: drop the bracket group if any of these appear inside it.
PROMO_RE = re.compile(
    r"\b(?:out\s+now|free\s*(?:dl|download)|buy|stream|forthcoming|unreleased|premiere|"
    r"coming\s+soon|available|pre-?order|support(?:ed)?\s+by|played\s+by)\b",
    re.I,
)
REMIX_RE = re.compile(
    r"\b(?:remix|rmx|re-?edit|edit|rework|re-?work|bootleg|flip|vip|dub|mix|version|refix|"
    r"mash-?up|cover|remode|remake|interpretation|reprise|acoustic|instrumental|live)\b",
    re.I,
)
FEAT_RE = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s", re.I)
FEAT_STRIP_RE = re.compile(r"\s*[\(\[]?\b(?:feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]?", re.I)
ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,|&|\+|\band\b|\bx\b|\bvs\.?(?=\s)|\bfeat\.?|\bft\.?|\bfeaturing\b|\bmeets\b)\s*", re.I
)
TRAILING_PROMO_RE = re.compile(
    r"\s*[-–—:|]?\s*(?:free\s*(?:dl|download)|out\s+now|forthcoming|unreleased|coming\s+soon)\b.*$", re.I
)
VERSION_SUFFIX_RE = re.compile(r"\s+[-–—]\s+(?P<v>[^-–—()]+)$")
ARTIST_TITLE_SEP_RE = re.compile(r"\s+[-–—−~]+\s*|\s*[-–—−~]+\s+|[–—]")
QUOTED_TITLE_RE = re.compile(r"^(?P<artist>.+?)\s+[\"“](?P<title>.+?)[\"”]\s*$")
WITH_SPLIT_RE = re.compile(r"\s+(?:w/|with/)\s*", re.I)

_ID_VALUES = {"id", "unknown", "tba", "tbc", "unreleased", "?", "??", "???", "track id", "id id"}
_SKIP_TITLES = {
    "intro", "outro", "tracklist", "tracklisting", "track list", "setlist", "start", "end",
    "break", "interlude", "applause", "talking", "speech",
}
_EDGE_CHARS = " \t-–—|:;,•·*\"'“”‘’>~_/\\"
_SMART_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "´": "'", "`": "'"})


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass
class Track:
    index: int
    artist: str
    title: str
    timestamp: str | None = None
    seconds: int | None = None
    raw: str = ""

    @property
    def artists(self) -> list[str]:
        return [a.strip() for a in ARTIST_SPLIT_RE.split(self.artist) if a and a.strip()]

    @property
    def primary_artist(self) -> str:
        artists = self.artists
        return artists[0] if artists else ""

    @property
    def remix(self) -> str | None:
        """Remix / edit credit from the title, e.g. ``"Fisher Remix"``."""
        for match in re.finditer(r"\(([^()]*)\)", self.title):
            inner = match.group(1).strip()
            if REMIX_RE.search(inner) and not FEAT_RE.search(inner + " "):
                return inner
        return None

    @property
    def featured(self) -> list[str]:
        found = []
        for m in re.finditer(r"\b(?:feat\.?|ft\.?|featuring)\s+([^()\[\]]+)", self.title, re.I):
            found.extend(a for a in ARTIST_SPLIT_RE.split(m.group(1)) if a.strip())
        return [a.strip() for a in found]

    @property
    def base_title(self) -> str:
        """Title with every parenthetical (remix, feat.) removed."""
        base = FEAT_STRIP_RE.sub(" ", self.title)
        base = re.sub(r"\([^()]*\)", " ", base)
        base = _collapse(base).strip(_EDGE_CHARS)
        return base or self.title

    @property
    def query_title(self) -> str:
        """Title for free-text search: feat. removed, parentheses flattened."""
        text = FEAT_STRIP_RE.sub(" ", self.title)
        return _collapse(re.sub(r"[()\[\]]", " ", text))

    @property
    def display(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "artist": self.artist,
            "title": self.title,
            "timestamp": self.timestamp,
            "seconds": self.seconds,
            "raw": self.raw,
            "display": self.display,
            "remix": self.remix,
            "base_title": self.base_title,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        seconds = data.get("seconds")
        return cls(
            index=int(data.get("index") or 0),
            artist=str(data.get("artist") or "").strip(),
            title=str(data.get("title") or "").strip(),
            timestamp=data.get("timestamp") or None,
            seconds=int(seconds) if seconds not in (None, "") else None,
            raw=str(data.get("raw") or ""),
        )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def parse_tracklist(text: str, *, loose: bool = False) -> list[Track]:
    """Parse a description / pasted tracklist into tracks.

    Strategy (first one that finds >= 2 tracks wins):
      1. lines with a leading or trailing timestamp
      2. numbered lines ("1. Artist - Title")
      3. (``loose=True`` only) any line containing an "Artist - Title" separator
    """
    lines = _split_lines(text or "")

    tracks = _parse_timestamped(lines)
    if len(tracks) < 2:
        numbered = _parse_numbered(lines)
        if len(numbered) > len(tracks):
            tracks = numbered
    if loose and len(tracks) < 2:
        plain = _parse_plain(lines)
        if len(plain) > len(tracks):
            tracks = plain
    return _finalize(tracks)


def parse_single(text: str) -> Track | None:
    """Parse one free-form line (e.g. a manually edited search) into a Track."""
    line = (text or "").strip()
    if not line:
        return None
    m = LEADING_TS_RE.match(line)
    if m and _ts_seconds(m.group("ts")) is not None:
        return _make_track(m.group("rest"), m.group("ts"), line, 1)
    return _make_track(line, None, line, 1, require_artist=False)


def clean_text(text: str) -> str:
    """Strip URLs, handles, emoji, labels and noise tags from a line."""
    s = unicodedata.normalize("NFKC", text).translate(_SMART_QUOTES)
    s = URL_RE.sub(" ", s)
    s = HANDLE_RE.sub(" ", s)
    s = _strip_symbols(s)
    for _ in range(2):  # second pass handles one level of nesting
        s = BRACKET_RE.sub(_bracket_sub, s)
    s = re.sub(r"[\[\]{}]", " ", s)  # unbalanced leftovers
    s = TRAILING_PROMO_RE.sub("", s)
    s = _pick_pipe_segment(s)
    return _collapse(s)


def split_artist_title(text: str) -> tuple[str, str]:
    m = ARTIST_TITLE_SEP_RE.search(text)
    if m and m.start() > 0 and m.end() < len(text):
        return text[: m.start()], text[m.end():]
    q = QUOTED_TITLE_RE.match(text)
    if q:
        return q.group("artist"), q.group("title")
    return "", text


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _split_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stamps = list(_ANY_TS_RE.finditer(line))
        if len(stamps) >= 3:  # whole tracklist crammed onto one line
            starts = [m.start() for m in stamps]
            if line[: starts[0]].strip():
                out.append(line[: starts[0]])
            for i, start in enumerate(starts):
                end = starts[i + 1] if i + 1 < len(starts) else len(line)
                out.append(line[start:end])
        else:
            out.append(line)
    return [ln for ln in out if ln.strip()]


def _parse_timestamped(lines: list[str]) -> list[Track]:
    tracks: list[Track] = []
    for line in lines:
        m = LEADING_TS_RE.match(line) or TRAILING_TS_RE.match(line)
        if not m or _ts_seconds(m.group("ts")) is None:
            continue
        track = _make_track(m.group("rest"), m.group("ts"), line, len(tracks) + 1, require_artist=False)
        if track:
            tracks.append(track)
    return tracks


def _parse_numbered(lines: list[str]) -> list[Track]:
    tracks: list[Track] = []
    for line in lines:
        m = NUMBERED_RE.match(line)
        if not m:
            continue
        track = _make_track(m.group("rest"), None, line, len(tracks) + 1, require_artist=True)
        if track:
            tracks.append(track)
    return tracks


def _parse_plain(lines: list[str]) -> list[Track]:
    tracks: list[Track] = []
    for line in lines:
        track = _make_track(line, None, line, len(tracks) + 1, require_artist=True)
        if track:
            tracks.append(track)
    return tracks


def _make_track(
    rest: str, ts: str | None, raw: str, index: int, *, require_artist: bool = False
) -> Track | None:
    rest = _LEADING_TS_STRIP_RE.sub("", rest)  # "00:00 - 03:15 Artist - Title"
    rest = re.sub(rf"\s*[\[\(]?(?<![\d:]){_TS}[\]\)]?\s*$", "", rest)
    text = clean_text(rest)
    if not text:
        return None

    artist, title = split_artist_title(text)
    artist = _clean_artist(artist)
    title = _clean_title(title)

    if _is_id(artist):
        artist = ""
    if not title or _is_id(title):
        return None
    if require_artist and not artist:
        return None
    if not artist and (title.lower() in _SKIP_TITLES or len(title) < 2):
        return None
    if not artist and not re.search(r"[^\W\d_]", title):
        return None

    return Track(
        index=index,
        artist=artist,
        title=title,
        timestamp=_normalize_ts(ts) if ts else None,
        seconds=_ts_seconds(ts) if ts else None,
        raw=raw.strip(),
    )


def _clean_artist(artist: str) -> str:
    artist = _collapse(artist).strip(_EDGE_CHARS)
    artist = re.sub(r"\s*\(\s*\)\s*", " ", artist)
    return _collapse(artist)


def _clean_title(title: str) -> str:
    title = _collapse(title).strip(_EDGE_CHARS)
    title = WITH_SPLIT_RE.split(title)[0]  # "Title w/ Other Track" mashup notes

    # Beatport style: "Title - Original Mix" / "Title - Fisher Remix"
    m = VERSION_SUFFIX_RE.search(title)
    if m:
        version = m.group("v").strip()
        if NOISE_TAG_RE.match(version):
            title = title[: m.start()]
        elif REMIX_RE.search(version):
            title = f"{title[: m.start()]} ({version})"

    title = re.sub(r"\(\s*\)", " ", title)
    title = _collapse(title).strip(_EDGE_CHARS)
    if title.startswith('"') and title.endswith('"'):
        title = title[1:-1]
    return title.strip()


def _bracket_sub(match: re.Match) -> str:
    opener, inner = match.group(1), match.group(2).strip()
    if not inner or opener == "{":
        return " "
    if NOISE_TAG_RE.match(inner) or PROMO_RE.search(inner):
        return " "
    if URL_RE.search(inner):
        return " "
    if opener == "[" and not REMIX_RE.search(inner) and not FEAT_RE.search(inner + " "):
        return " "  # square brackets usually hold the record label
    if _ANY_TS_RE.fullmatch(f"({inner})") or _ANY_TS_RE.fullmatch(inner):
        return " "
    return f" ({inner}) "


def _pick_pipe_segment(text: str) -> str:
    parts = [p for p in re.split(r"\s+\|\s+|\s*\|\s*$|^\s*\|\s*", text) if p and p.strip()]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    for part in parts:
        if ARTIST_TITLE_SEP_RE.search(part.strip()):
            return part
    return parts[0]


def _strip_symbols(text: str) -> str:
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("So", "Cs", "Co", "Cn", "Cf") or ch == "️":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_id(value: str) -> bool:
    v = value.strip().lower().strip("?!.- ")
    return value.strip().lower() in _ID_VALUES or v in _ID_VALUES or (bool(value.strip()) and not v)


def _ts_seconds(ts: str | None) -> int | None:
    if not ts:
        return None
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        h, m, s = parts
        if m >= 60 or s >= 60:
            return None
        return h * 3600 + m * 60 + s
    m, s = parts
    if s >= 60:
        return None
    return m * 60 + s


def _normalize_ts(ts: str) -> str:
    seconds = _ts_seconds(ts) or 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _finalize(tracks: list[Track]) -> list[Track]:
    seen: set[tuple[str, str]] = set()
    out: list[Track] = []
    for t in tracks:
        key = (t.artist.lower(), t.title.lower())
        if key in seen:
            continue
        seen.add(key)
        t.index = len(out) + 1
        out.append(t)
    return out
