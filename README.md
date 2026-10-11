# DJ Set → Spotify Playlist

Paste a YouTube link to a DJ set and get a Spotify playlist of every track in it.

The app reads the tracklist from the video description (or the comments, if the description doesn't have one), finds each track on Spotify, and creates the playlist on your account. It also includes a guide for turning that playlist into a DJ-style mix with Spotify's built-in **Mix** feature.

It runs two ways:

- **Web app** (`python app.py`): a browser UI where you review matches, fix searches by hand, untick tracks and create the playlist.
- **CLI** (`python cli.py <url>`): one command from link to playlist.

## Features

- **Finds the tracklist automatically.** It checks the video description first, then YouTube chapters, then the top comments, where tracklists are often pinned. You can also paste a tracklist in yourself.
- **Cleans up messy tracklists.** It handles timestamps (`01:23`, `1:02:03`, `[01:23]`, timestamps at the end of a line) and numbered lists. It strips URLs, @handles, emoji, record labels, promo text (`FREE DOWNLOAD`, `Out now on…`) and tags like `(Original Mix)` or `(Extended Mix)`. Remix credits are kept, and `ID - ID` lines are skipped.
- **Smart Spotify matching.** It tries up to five searches per track, from most specific to least, and scores each result on title and artist similarity. Scoring prefers the correct remix or original version. Lines that only give a song title (common in sets where the DJ plays their own music) are searched with the DJ's name, taken from the video title.
- **Review before creating.** Every track shows its match with album art and a confidence score. Uncertain matches are flagged. You can edit a search and retry, choose public or private, and download a list of unmatched tracks.
- **Mix guide and transition checker.** Step-by-step instructions for Spotify's Mix feature, plus a checker: enter the BPM and key of two songs and it recommends a preset (Blend, Rise, Fade or Auto) and transition length, based on tempo difference and key compatibility on the Camelot wheel.

## Setup

### 1. Install (Python 3.10+)

```bash
git clone https://github.com/JonathanCruzDavid/djset-to-spotify.git
cd djset-to-spotify
python -m venv .venv
```

Activate the environment:
- Windows (PowerShell): `.\.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Then install and create your settings file:
```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

### 2. Create a Spotify Developer app (required)

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. Click **Create app**.
   - **Redirect URI:** add exactly `http://127.0.0.1:8888/callback`. Spotify doesn't accept `localhost`.
   - **APIs used:** tick **Web API**.
3. Open the app's **Settings** and copy the **Client ID** and **Client secret** into `.env`:
   ```
   SPOTIPY_CLIENT_ID=...
   SPOTIPY_CLIENT_SECRET=...
   SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
   ```
4. Under **User Management**, add the Spotify account(s) that will use the app. New apps run in Development Mode, where only listed users can log in.

### 3. Create a YouTube API key (optional, recommended)

Without a key the app uses [yt-dlp](https://github.com/yt-dlp/yt-dlp), which works but is slower.

1. In <https://console.cloud.google.com/>, create or select a project.
2. Go to **APIs & Services → Library**, search for **YouTube Data API v3** and click **Enable**.
3. Go to **APIs & Services → Credentials → Create credentials → API key**.
4. Under **API restrictions**, choose **Restrict key** and select **YouTube Data API v3**.
5. Add it to `.env` as `YOUTUBE_API_KEY=...`.

The free quota (10,000 units per day) is far more than you'll need; each video uses about 1–2 units.

### 4. Session secret

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste the output into `.env` as `FLASK_SECRET_KEY=...`.

## Usage

### Web app

```bash
python app.py
```
Open **<http://127.0.0.1:8888>**. Use `127.0.0.1`, not `localhost`, or the Spotify login won't work.

1. Click **Connect Spotify**.
2. Paste a YouTube link and click **Get tracklist**. Matching starts automatically.
3. Review the matches. Green badges are confident matches; yellow **check** badges need a look. Click ✎ to edit a search.
4. Name the playlist, choose public or private, and click **Create playlist**.
5. Follow **Step 4: Mix it in Spotify** to add DJ-style transitions.

### CLI

```bash
python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --public
```
The first run opens a browser to log in to Spotify; after that, the token is cached locally.

| Option | What it does |
| --- | --- |
| `-n, --name "..."` | Playlist name (default: the video title) |
| `--public` / `--private` | Visibility (default: private) |
| `-t, --tracklist-file FILE` | Use a tracklist from a text file |
| `--include-uncertain` | Also add low-confidence matches |
| `--dry-run` | Match tracks without creating a playlist |
| `--parse-only` | Print the parsed tracklist only (no Spotify needed) |
| `--no-comments` | Don't search the comments for a tracklist |
| `-u, --unmatched-file PATH` | Where to write the unmatched-tracks report |
| `-v, --verbose` | Debug logging |

## Mixing your playlist like a DJ

Spotify Premium has a built-in **Mix** feature that adds DJ-style transitions to a playlist. Third-party apps can't control it; Spotify offers no API for it, and its developer terms don't allow other apps to mix tracks. So the app guides you instead of mixing for you:

1. Open the playlist in Spotify and click **Mix**. Spotify's AI sets an **Auto** transition between every song, and you can play it right away.
2. Preview the transitions and look for big tempo jumps (more than about 5 BPM) or clashing keys.
3. Fix only those:

| If the two songs… | Preset | Length |
| --- | --- | --- |
| Are close in tempo and the keys match | **Blend** | 16 bars |
| Step up in energy or tempo, keys OK | **Rise** | 8 bars |
| Have a big tempo jump or clashing keys | **Fade** | 4 bars |
| Already sound good | **Auto** | leave it |

The **transition checker** on the web page applies these rules for you.

## Keeping your keys private

- All keys live only in `.env`, which is listed in `.gitignore` and never committed. Only `.env.example`, with blank values, is in the repo.
- Login tokens are also ignored: the CLI's `.spotify_cache` and the web app's session cookie.
- Before pushing, run `git status` and confirm `.env` isn't listed.
- If a key ever leaks, rotate it: **Rotate client secret** in the Spotify dashboard, or delete and recreate the key in Google Cloud.

## Project layout

```
djset-to-spotify/
├── app.py                    # web entry point
├── cli.py                    # CLI entry point
├── requirements.txt
├── .env.example              # template; copy to .env
├── djset2spotify/
│   ├── config.py             # settings from .env
│   ├── youtube.py            # YouTube Data API + yt-dlp fallback, comments, chapters
│   ├── parser.py             # tracklist parsing and cleanup
│   ├── spotify_client.py     # OAuth, search and scoring, playlist creation
│   ├── pipeline.py           # shared steps: load → match → create → report
│   ├── cli.py                # command-line interface
│   ├── webapp.py             # Flask routes and JSON API
│   ├── templates/index.html  # web UI
│   └── static/               # app.js, style.css
└── tests/                    # pytest suite
```

Run the tests with `pip install -r requirements-dev.txt` and then `pytest`.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `INVALID_CLIENT: Invalid redirect URI` | The redirect URI in `.env` must exactly match the one in the Spotify dashboard. |
| 403 errors from Spotify | Add your account under **User Management** in the Spotify dashboard, then log out and back in. |
| `auth_error=state_mismatch` | Open the app at `127.0.0.1`, not `localhost`. |
| "No tracklist found" | The video has no tracklist in its description, chapters or top comments. Paste one manually. |
| yt-dlp errors | Run `pip install -U yt-dlp`. |
| Wrong version matched | Click ✎ and search for e.g. `Artist - Title (Remixer Remix)`, or untick the track. |
