import pytest

from djset2spotify.parser import Track, clean_text, parse_single, parse_tracklist
from djset2spotify.spotify_client import build_queries, score_candidate
from djset2spotify.youtube import YouTubeError, extract_video_id

DESCRIPTION = """\
Recorded live at Printworks London 🔥
Follow me: https://instagram.com/somedj @somedj #housemusic

Tracklist:
00:00 Intro
01:23 Bicep - Glue (Original Mix) [Ninja Tune]
05:10 - Fred again.. & Swedish House Mafia – Turn On The Lights again.. (Extended Mix)
[09:45] Peggy Gou - (It Goes Like) Nanana [XL Recordings]
14:02 | Chris Lake feat. Aatig - Turn Off The Lights (Fisher Remix) [FREE DOWNLOAD]
18:30 ID - ID
22:15 Rufus Du Sol - Innerbloom - What So Not Remix
1:02:03 Disclosure - Latch (feat. Sam Smith) https://spoti.fi/xyz
1:10:00 Outro
"""


def test_parses_timestamped_description():
    tracks = parse_tracklist(DESCRIPTION)
    displays = [t.display for t in tracks]
    assert displays == [
        "Bicep - Glue",
        "Fred again.. & Swedish House Mafia - Turn On The Lights again..",
        "Peggy Gou - (It Goes Like) Nanana",
        "Chris Lake feat. Aatig - Turn Off The Lights (Fisher Remix)",
        "Rufus Du Sol - Innerbloom (What So Not Remix)",
        "Disclosure - Latch (feat. Sam Smith)",
    ]
    assert tracks[0].timestamp == "01:23" and tracks[0].seconds == 83
    assert tracks[-1].timestamp == "1:02:03" and tracks[-1].seconds == 3723
    assert [t.index for t in tracks] == list(range(1, 7))


def test_track_helpers():
    t = Track(1, "Chris Lake feat. Aatig", "Turn Off The Lights (Fisher Remix)")
    assert t.primary_artist == "Chris Lake"
    assert t.artists == ["Chris Lake", "Aatig"]
    assert t.remix == "Fisher Remix"
    assert t.base_title == "Turn Off The Lights"
    assert t.query_title == "Turn Off The Lights Fisher Remix"

    t2 = Track(2, "Disclosure", "Latch (feat. Sam Smith)")
    assert t2.remix is None
    assert t2.base_title == "Latch"
    assert t2.featured == ["Sam Smith"]


def test_trailing_timestamps_and_numbering():
    text = "1. Bicep - Glue 0:00\n2. Four Tet - Baby 4:30\n3. Floating Points - LesAlpx (5:55)"
    assert [t.display for t in parse_tracklist(text)] == [
        "Bicep - Glue", "Four Tet - Baby", "Floating Points - LesAlpx",
    ]


def test_numbered_without_timestamps():
    text = "Tracklist\n1. Bicep - Glue\n2) Four Tet - Baby\n03 - Caribou - Never Come Back\nThanks for listening"
    assert [t.display for t in parse_tracklist(text)] == [
        "Bicep - Glue", "Four Tet - Baby", "Caribou - Never Come Back",
    ]


def test_single_line_tracklist():
    text = "00:00 Bicep - Glue 03:10 Four Tet - Baby 07:20 Caribou - Sun"
    assert [t.display for t in parse_tracklist(text)] == ["Bicep - Glue", "Four Tet - Baby", "Caribou - Sun"]


def test_loose_mode_only_for_manual():
    text = "Bicep - Glue\nFour Tet - Baby"
    assert parse_tracklist(text) == []
    assert len(parse_tracklist(text, loose=True)) == 2


def test_ignores_non_tracklist_description():
    text = "New video every Friday!\nSubscribe - it helps a lot\nShot at 12:30 in Berlin"
    assert parse_tracklist(text) == []


def test_dedupes_and_mashups():
    text = "00:00 Bicep - Glue\n03:00 Bicep - Glue\n06:00 Four Tet - Baby w/ Burial - Archangel"
    assert [t.display for t in parse_tracklist(text)] == ["Bicep - Glue", "Four Tet - Baby"]


def test_clean_text_strips_noise():
    assert clean_text("Bicep - Glue (Original Mix) [Ninja Tune] 🎧 https://x.y") == "Bicep - Glue"
    assert clean_text("A - B [Fisher Remix]") == "A - B (Fisher Remix)"
    assert clean_text("A - B (Out now on Toolroom)") == "A - B"


def test_parse_single():
    t = parse_single("Bicep - Glue (Extended Mix)")
    assert (t.artist, t.title) == ("Bicep", "Glue")
    assert parse_single("Glue").title == "Glue"


def test_queries_fall_back_without_remix():
    t = Track(1, "Chris Lake", "Turn Off The Lights (Fisher Remix)")
    qs = build_queries(t)
    assert qs[0] == "Chris Lake Turn Off The Lights Fisher Remix"
    assert "track:Turn Off The Lights artist:Chris Lake" in qs
    assert "Chris Lake Turn Off The Lights" in qs


def _item(name, *artists):
    return {"name": name, "artists": [{"name": a} for a in artists], "uri": "spotify:track:x"}


def test_scoring_prefers_right_version():
    t = Track(1, "Chris Lake", "Turn Off The Lights (Fisher Remix)")
    remix = score_candidate(t, _item("Turn Off The Lights - Fisher Remix", "Chris Lake", "Fisher"))
    original = score_candidate(t, _item("Turn Off The Lights", "Chris Lake"))
    wrong = score_candidate(t, _item("Lights Out", "Someone Else"))
    assert remix > original > wrong
    assert remix >= 0.62 and wrong < 0.45


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=120s&list=abc",
        "https://youtu.be/dQw4w9WgXcQ?si=xyz",
        "youtube.com/live/dQw4w9WgXcQ",
        "https://m.youtube.com/shorts/dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "dQw4w9WgXcQ",
    ],
)
def test_extract_video_id(url):
    assert extract_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", ["https://vimeo.com/123", "https://youtube.com/channel/abc", ""])
def test_extract_video_id_rejects(url):
    with pytest.raises(YouTubeError):
        extract_video_id(url)


def test_unknown_remix_credit_dropped():
    t = parse_single("Danny Tenaglia - Music Is The Answer (ID Remix)")
    assert t.title == "Music Is The Answer" and t.remix is None


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Ninajirachi - live NYC @ the Brooklyn Steel (10/5/26)", "Ninajirachi"),
        ("Fred again.. | Boiler Room: London", "Fred again.."),
        ("Four Tet live at Alexandra Palace", "Four Tet"),
        ("Boiler Room x Dekmantel", None),
    ],
)
def test_guess_set_artist(title, expected):
    from djset2spotify.pipeline import guess_set_artist

    assert guess_set_artist(title) == expected
