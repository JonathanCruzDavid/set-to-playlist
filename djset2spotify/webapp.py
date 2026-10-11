"""Flask web app: browser UI + JSON API over the same pipeline as the CLI.

Routes
    GET  /                 single-page UI
    GET  /login            start Spotify OAuth
    GET  /callback         OAuth redirect target (must match SPOTIPY_REDIRECT_URI)
    GET  /logout
    GET  /api/status       config + login state
    POST /api/tracklist    {url, tracklist?}          -> video + parsed tracks
    POST /api/search       {track} | {query}          -> one match result
    POST /api/playlist     {name, public, description, uris, unmatched?} -> playlist
"""

from __future__ import annotations

import logging
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from spotipy.cache_handler import FlaskSessionCacheHandler
from spotipy.exceptions import SpotifyException, SpotifyOauthError

from .config import ConfigError, Settings
from .parser import Track, parse_single
from .pipeline import build_playlist, default_description, load_tracklist, write_unmatched_report
from .spotify_client import MatchResult, SpotifyService, build_client, build_oauth
from .youtube import VideoInfo, YouTubeError

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    if settings.flask_secret_key:
        app.secret_key = settings.flask_secret_key
    else:
        log.warning("FLASK_SECRET_KEY not set; using a random key (logins reset on restart).")
        app.secret_key = secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    # ------------------------------------------------------------------ helpers

    def oauth(state: str | None = None):
        return build_oauth(
            settings, cache_handler=FlaskSessionCacheHandler(session), state=state, open_browser=False
        )

    def current_service() -> SpotifyService | None:
        if settings.missing_spotify():
            return None
        manager = oauth()
        token = manager.validate_token(manager.cache_handler.get_cached_token())
        return SpotifyService(build_client(manager)) if token else None

    def require_spotify(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            service = current_service()
            if service is None:
                raise ApiError("Connect your Spotify account first.", 401)
            return fn(service, *args, **kwargs)

        return wrapper

    def body() -> dict:
        if not request.is_json:
            raise ApiError("Expected a JSON body.", 415)
        return request.get_json(silent=True) or {}

    # ------------------------------------------------------------------ errors

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return jsonify(error=exc.message), exc.status

    @app.errorhandler(ConfigError)
    def _config_error(exc: ConfigError):
        return jsonify(error=str(exc)), 500

    @app.errorhandler(YouTubeError)
    def _yt_error(exc: YouTubeError):
        return jsonify(error=str(exc)), 422

    @app.errorhandler(SpotifyException)
    def _spotify_error(exc: SpotifyException):
        if exc.http_status == 401:
            session.pop("token_info", None)
            return jsonify(error="Spotify session expired. Please reconnect."), 401
        if exc.http_status == 403:
            return jsonify(error=(
                f"Spotify refused the request (403: {exc.msg}). If your app is in Development Mode, "
                "make sure your account is listed under User Management in the developer dashboard."
            )), 403
        return jsonify(error=f"Spotify error: {exc.msg}"), 502

    # ------------------------------------------------------------------ pages

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/login")
    def login():
        state = secrets.token_urlsafe(24)
        session["oauth_state"] = state
        return redirect(oauth(state).get_authorize_url())

    @app.get("/callback")
    def callback():
        if request.args.get("error"):
            return redirect(url_for("index", auth_error=request.args["error"]))
        expected = session.pop("oauth_state", None)
        if not expected or request.args.get("state") != expected:
            return redirect(url_for("index", auth_error="state_mismatch"))
        try:
            oauth().get_access_token(request.args.get("code"), as_dict=False, check_cache=False)
        except SpotifyOauthError as exc:
            log.warning("OAuth exchange failed: %s", exc)
            return redirect(url_for("index", auth_error="token_exchange_failed"))
        return redirect(url_for("index"))

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    # ------------------------------------------------------------------ API

    @app.get("/api/status")
    def status():
        user = None
        service = current_service()
        if service:
            try:
                me = service.me()
                images = me.get("images") or []
                user = {
                    "id": me["id"],
                    "name": me.get("display_name") or me["id"],
                    "image": images[0]["url"] if images else None,
                }
            except SpotifyException:
                session.pop("token_info", None)
        redirect_host = urlparse(settings.spotify_redirect_uri)
        return jsonify(
            spotify={"configured": not settings.missing_spotify(), "connected": user is not None, "user": user},
            youtube={"api_key": bool(settings.youtube_api_key)},
            missing=settings.missing_spotify(),
            redirect_uri=settings.spotify_redirect_uri,
            expected_origin=f"{redirect_host.scheme}://{redirect_host.netloc}",
        )

    @app.post("/api/tracklist")
    def tracklist():
        data = body()
        url = (data.get("url") or "").strip() or None
        manual = (data.get("tracklist") or "").strip() or None
        if not url and not manual:
            raise ApiError("Paste a YouTube URL (or a tracklist).")
        result = load_tracklist(url, settings, manual_text=manual)
        payload = result.to_dict()
        payload["default_description"] = default_description(result.video)
        return jsonify(payload)

    @app.post("/api/search")
    @require_spotify
    def search(service: SpotifyService):
        data = body()
        if data.get("query"):
            track = parse_single(str(data["query"]))
            if track is None:
                raise ApiError("Couldn't understand that search. Try 'Artist - Title'.")
            track.index = int(data.get("index") or track.index)
            original = data.get("track") or {}
            track.timestamp = original.get("timestamp") or track.timestamp
            track.seconds = original.get("seconds") or track.seconds
        elif data.get("track"):
            track = Track.from_dict(data["track"])
            if not track.title:
                raise ApiError("Track has no title.")
        else:
            raise ApiError("Provide 'track' or 'query'.")
        context = str(data.get("set_artist") or "").strip()[:100] or None
        return jsonify(service.match(track, context).to_dict())

    @app.post("/api/playlist")
    @require_spotify
    def playlist(service: SpotifyService):
        data = body()
        uris = [u for u in data.get("uris") or [] if isinstance(u, str) and u.startswith("spotify:track:")]
        if not uris:
            raise ApiError("No tracks selected.")
        name = (data.get("name") or "").strip() or "DJ Set"
        result = build_playlist(
            service,
            name,
            uris,
            public=bool(data.get("public")),
            description=str(data.get("description") or ""),
        )

        unmatched = data.get("unmatched") or []
        if unmatched:
            video = VideoInfo(video_id=str(data.get("video_id") or ""), title=name, description="")
            path = write_unmatched_report(
                [MatchResult(track=Track.from_dict(t)) for t in unmatched], video, settings.unmatched_dir
            )
            result["unmatched_file"] = str(path) if path else None
        return jsonify(result)

    return app


def run() -> None:
    import os

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    parsed = urlparse(settings.spotify_redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8888
    if host not in ("127.0.0.1", "localhost", "::1"):
        host = os.getenv("HOST", "127.0.0.1")
        port = int(os.getenv("PORT", "8888"))
    print(f"\n  Set to Playlist running at http://{host}:{port}\n")
    create_app(settings).run(host=host, port=port, debug=os.getenv("FLASK_DEBUG") == "1")
