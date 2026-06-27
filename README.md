# Media Downloader

A small **local web app** for downloading video and images from YouTube,
Facebook, Instagram, TikTok, and X (Twitter). It runs entirely on your own
machine — paste a link in your browser, pick options, and the files land in a
folder of your choosing.

Built on two engines:

- [**yt-dlp**](https://github.com/yt-dlp/yt-dlp) — videos & audio
- [**gallery-dl**](https://github.com/mikf/gallery-dl) — image posts & galleries

plus **ffmpeg** (merging HD video+audio, MP3 conversion, embedding subtitles).

## Features

- 🎬 **Video** with quality selection (Best / 4K / 1440p / 1080p / 720p / 480p / 360p)
- 🎵 **Audio** extraction to **MP3** (192 kbps)
- 🖼️ **Images & media** — photo posts and carousels (and any videos in them)
- 📃 **Playlists & channels** (YouTube) — grab the whole thing in one click
- 💬 **Subtitles** (YouTube) — download captions and embed them
- 🔑 **Use login from your browser** — optional cookies for posts that require being signed in
- 📁 **Choose where to save** — native folder picker (or paste any path); remembered between sessions
- 🔎 **Reveal in Explorer** — jump straight to any downloaded file
- 📊 Live progress with speed and ETA

## Quick start (Windows)

Double-click **`run.bat`**. It creates a virtual environment, installs
dependencies the first time, then launches the app and opens your browser at
<http://127.0.0.1:5005>.

## Manual start (any OS)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5005>.

## How it works

1. Paste a link and click **Fetch**. The app detects the platform and shows a preview.
2. Choose what to grab:
   - **Video** / **Audio (MP3)** → handled by yt-dlp (with quality selection).
   - **Images & media** → handled by gallery-dl (downloads every photo/video in the post).
3. Pick a **Save to** folder, then **Download**. Watch progress, then **Reveal** any file.

## Logging in (Instagram, Facebook, private/age-gated posts)

Many posts — especially on **Instagram** and **Facebook** — only load if you're
signed in. Pick a login source from the **“Use login from”** dropdown:

- **Cookies.txt file… (most reliable)** — export your cookies once and point the
  app at the file. Recommended on Windows, because reading cookies directly from
  Chrome/Edge usually fails (they encrypt their cookie store — "app-bound
  encryption").
- **Firefox / Chrome / Edge / Brave** — read cookies straight from the browser.
  Only Firefox is reliable on Windows; Chrome/Edge often fail and must be fully
  closed first.

### Exporting a cookies.txt file

1. Install a cookies-export extension in the browser where you're logged in — e.g.
   **“Get cookies.txt LOCALLY”** (Chrome/Edge) or **“cookies.txt”** (Firefox).
2. Log into the site (e.g. instagram.com) in that browser.
3. Click the extension and **Export / Save** the `cookies.txt` file somewhere.
4. In the app, choose **“Cookies.txt file…”** under *Use login from* and select
   that file. Your choice is remembered between sessions.

Treat that file like a password — it grants access to your logged-in session.
Cookies expire, so re-export if downloads start failing with login errors again.

> **YouTube cookies expired?** When YouTube starts failing again with *"Sign in
> to confirm you're not a bot,"* your `cookies.txt` has gone stale. See
> [`READ-ME-WHEN-YOUTUBE-BREAKS.txt`](READ-ME-WHEN-YOUTUBE-BREAKS.txt) in the app
> folder for the 3-minute re-export fix (with a command to verify the new file).

### Tip: load it automatically

Instead of picking the file every time, drop your exported file (named exactly
`cookies.txt`) into the **app folder** — the same folder as `app.py` / `run.bat`.
The app then uses it automatically for every download, unless you choose a
different option in the dropdown. When it's detected, the dropdown's default reads
*“App cookies.txt (default)”* and a green note confirms it.

### Reliability by platform (rough guide)

| Platform   | Video            | Images           |
|------------|------------------|------------------|
| YouTube    | ✅ excellent      | n/a              |
| X (Twitter)| ✅ good           | ✅ good           |
| TikTok     | ✅ good           | ✅ photo posts    |
| Instagram  | ⚠️ login often needed | ⚠️ login often needed |
| Facebook   | ⚠️ variable       | ⚠️ login + variable |

These sites change constantly and actively discourage downloading. When something
stops working, updating the engines usually helps (see below).

## YouTube setup (PO token + JS runtime)

Modern YouTube blocks plain yt-dlp with **“Sign in to confirm you're not a bot.”**
Cookies and `player_client` tweaks no longer get past it on their own. The fix is
two one-time installs that let yt-dlp prove it's a genuine client:

1. **A JavaScript runtime (Deno)** — solves YouTube's JS / "n-sig" challenges:

   ```powershell
   winget install --id DenoLand.Deno -e --source winget
   ```

   (`--source winget` avoids a certificate error on the Microsoft Store source
   that some networks/AV HTTPS-inspection setups trigger.)

2. **A PO-token provider** — mints the Proof-of-Origin tokens YouTube demands. It's
   a yt-dlp plugin (already in `requirements.txt`) plus a small Node server you
   build once. **The server version must match the plugin version** (`1.3.1`):

   ```powershell
   git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$HOME\bgutil-ytdlp-pot-provider"
   cd "$HOME\bgutil-ytdlp-pot-provider\server"
   npm ci
   npx tsc
   ```

   Requires [Node.js](https://nodejs.org/). The app auto-detects the built server
   in `~/bgutil-ytdlp-pot-provider` and finds Deno automatically (even if winget
   only added it to a future shell's PATH).

Restart the app — the startup banner should read `Deno (JS): ok` and
`PO token: ok`. If either shows **NOT FOUND / NOT SET UP**, YouTube may hit the
bot wall; redo the matching step.

> With both pieces in place you usually no longer need a YouTube `cookies.txt` for
> ordinary public videos. Cookies still help for age-restricted or members-only
> content.

## ffmpeg

ffmpeg is required for merging HD streams, MP3 conversion, and embedding
subtitles. If you don't have it installed, the app **automatically uses a bundled
copy** via the `imageio-ffmpeg` package — no action needed. For the full version
(adds `ffprobe`), optionally run `winget install Gyan.FFmpeg` and restart.

## Keeping it working

YouTube and the social platforms change often. If downloads start failing, update
the engines:

```powershell
.venv\Scripts\python.exe -m pip install -U yt-dlp gallery-dl
```

## Certificates

On Windows, some servers don't send their full certificate chain, which trips up
Python's bundled certificates (`unable to get local issuer certificate`). The app
uses [`truststore`](https://github.com/sethmlarson/truststore) to verify against
the **Windows certificate store** instead, which handles this automatically.

## A note on usage

Downloading content you don't own or that isn't offered under a permissive
license generally violates these platforms' Terms of Service. Please use this only
for content you have the right to download — your own posts, Creative Commons
material, archival of content you own, or offline viewing where permitted.

## Where files go

By default, files save to the `downloads/` folder next to `app.py`. Use the
**Save to** field — or **Browse…** for a native folder picker — to pick any
folder; your choice is remembered between sessions. Image posts and playlists get
their own subfolder. Each finished file has a **Reveal** button, and **Open
folder** opens the destination.
