"""Command-line interface.

    python cli.py "https://www.youtube.com/watch?v=..." [--public] [--name "My Playlist"]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ConfigError, Settings
from .pipeline import (
    build_playlist,
    default_description,
    load_tracklist,
    match_tracks,
    write_unmatched_report,
)
from .spotify_client import MatchResult, SpotifyService
from .youtube import YouTubeError

STATUS_ICON = {"matched": "OK ", "uncertain": "?? ", "unmatched": "-- "}


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="set-to-playlist",
        description="Turn a YouTube DJ set's tracklist into a Spotify playlist.",
    )
    p.add_argument("url", nargs="?", help="YouTube video URL (or ID)")
    p.add_argument("-n", "--name", help="Playlist name (default: the video title)")
    vis = p.add_mutually_exclusive_group()
    vis.add_argument("--public", action="store_true", help="Create a public playlist")
    vis.add_argument("--private", action="store_true", help="Create a private playlist (default)")
    p.add_argument("-t", "--tracklist-file", type=Path, help="Use a text file as the tracklist instead of the description")
    p.add_argument("--no-comments", action="store_true", help="Don't look in comments if the description has no tracklist")
    p.add_argument("--include-uncertain", action="store_true", help="Also add low-confidence matches")
    p.add_argument("--dry-run", action="store_true", help="Parse and match, but don't create a playlist")
    p.add_argument("--parse-only", action="store_true", help="Only print the parsed tracklist (no Spotify login needed)")
    p.add_argument("-u", "--unmatched-file", type=Path, help="Where to write unmatched tracks (default: ./unmatched/...)")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s" if args.verbose else "%(message)s",
    )
    for noisy in ("googleapiclient", "urllib3", "spotipy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not args.url and not args.tracklist_file:
        print("error: provide a YouTube URL and/or --tracklist-file", file=sys.stderr)
        return 2

    settings = Settings.from_env()
    try:
        return _run(args, settings)
    except (ConfigError, YouTubeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130


def _run(args: argparse.Namespace, settings: Settings) -> int:
    manual = args.tracklist_file.read_text(encoding="utf-8") if args.tracklist_file else None

    print("Fetching video and parsing tracklist...")
    result = load_tracklist(args.url, settings, manual_text=manual, check_comments=not args.no_comments)
    video, tracks = result.video, result.tracks
    print(f"\n  {video.title}" + (f"  ({video.channel})" if video.channel else ""))
    print(f"  {len(tracks)} tracks found in {result.source} (via {video.source or 'n/a'})\n")

    if not tracks:
        print("No tracklist found. Paste it into a text file and use --tracklist-file.", file=sys.stderr)
        return 1

    if args.parse_only:
        for t in tracks:
            print(f"  {t.index:>3}. {t.timestamp or '':>8}  {t.display}")
        return 0

    settings.require_spotify()
    service = SpotifyService.for_cli(settings)
    user = service.me()
    print(f"Logged in to Spotify as {user.get('display_name') or user['id']}\n")

    def report(r: MatchResult) -> None:
        found = f"{', '.join(r.artists)} - {r.name}" if r.uri else "no match"
        print(f"  {STATUS_ICON[r.status]}{r.track.index:>3}. {r.track.display[:55]:<55} -> {found} ({r.score:.2f})")

    results = match_tracks(service, tracks, on_result=report, context_artist=result.set_artist)
    accepted = [r for r in results if r.matched or (args.include_uncertain and r.status == "uncertain" and r.uri)]
    uncertain = sum(r.status == "uncertain" for r in results)
    print(f"\nMatched {len(accepted)}/{len(results)} tracks ({uncertain} uncertain).")

    unmatched_path = write_unmatched_report(
        [r for r in results if r not in accepted], video, settings.unmatched_dir, path=args.unmatched_file
    )
    if unmatched_path:
        print(f"Unmatched tracks written to {unmatched_path}")

    if args.dry_run:
        print("Dry run: no playlist created.")
        return 0
    if not accepted:
        print("Nothing to add; no playlist created.", file=sys.stderr)
        return 1

    playlist = build_playlist(
        service,
        args.name or video.title or "DJ Set",
        [r.uri for r in accepted],
        public=args.public,
        description=default_description(video),
    )
    print(f"\nCreated {'public' if args.public else 'private'} playlist '{playlist['name']}' "
          f"with {playlist['added']} tracks:\n  {playlist['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
