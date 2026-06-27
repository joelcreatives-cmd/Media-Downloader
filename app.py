"""
Media Downloader — local web app.

A friendly web UI on top of two engines:
  * yt-dlp     — videos & audio (YouTube, Facebook, Instagram, TikTok, X, …)
  * gallery-dl — image posts & mixed-media galleries
plus ffmpeg (merging HD video+audio, MP3 conversion, embedding subtitles).

Run with:  python app.py   (or double-click run.bat on Windows)
Then open: http://127.0.0.1:5005
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from urllib.parse import urlparse

# Verify TLS against the operating system's certificate store. On Windows this
# lets SChannel fetch missing intermediate certificates that OpenSSL/certifi
# cannot, fixing "unable to get local issuer certificate" failures. Must run
# before the download engines open any HTTPS connection.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

from flask import Flask, Response, jsonify, render_template, request
from yt_dlp import YoutubeDL

try:
    import tkinter
    from tkinter import filedialog

    TK_AVAILABLE = True
except Exception:  # noqa: BLE001
    TK_AVAILABLE = False

try:
    import gallery_dl  # noqa: F401

    GALLERYDL_OK = True
except Exception:  # noqa: BLE001
    GALLERYDL_OK = False

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(APP_DIR, "downloads")
BIN_DIR = os.path.join(APP_DIR, "bin")
PORT = 5005

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__)

# In-memory registry of running/finished download jobs.
# job_id -> {"q": Queue, "files": [paths], "finished": bool}
JOBS = {}

# Folders we've downloaded into this session. The reveal endpoint only opens
# files that live inside one of these (the default is always allowed).
KNOWN_DESTS = {os.path.realpath(DOWNLOAD_DIR)}

# Allow only one native folder dialog at a time.
_dialog_lock = threading.Lock()

# A native folder picker is possible on Windows (PowerShell) or anywhere tk exists.
BROWSE_AVAILABLE = os.name == "nt" or TK_AVAILABLE

# Browsers we'll accept for "use login from browser" cookie extraction.
ALLOWED_BROWSERS = {"firefox", "chrome", "edge", "brave", "chromium", "opera", "vivaldi", "safari"}


# --------------------------------------------------------------------------- #
#  ffmpeg discovery                                                           #
# --------------------------------------------------------------------------- #
def ensure_ffmpeg():
    """
    Return a directory path that contains an ffmpeg binary, or None.

    Prefers a system-installed ffmpeg (which also gives us ffprobe). If none is
    found, falls back to the binary bundled by the `imageio-ffmpeg` package,
    copying it to ./bin/ffmpeg.exe so yt-dlp can recognise it by name.
    """
    system = shutil.which("ffmpeg")
    if system:
        return os.path.dirname(system)

    try:
        import imageio_ffmpeg

        src = imageio_ffmpeg.get_ffmpeg_exe()
        os.makedirs(BIN_DIR, exist_ok=True)
        ext = ".exe" if os.name == "nt" else ""
        dst = os.path.join(BIN_DIR, "ffmpeg" + ext)
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
        return BIN_DIR
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Could not locate or bundle ffmpeg: {exc}")
        return None


FFMPEG_DIR = ensure_ffmpeg()


# --------------------------------------------------------------------------- #
#  YouTube bot-check bypass                                                    #
#                                                                              #
#  YouTube now rejects yt-dlp with "Sign in to confirm you're not a bot"       #
#  unless it can (a) mint a Proof-of-Origin (PO) token and (b) solve JS/n-sig  #
#  challenges. We satisfy both with:                                           #
#    * the bgutil-ytdlp-pot-provider plugin (auto-loaded; its Node server      #
#      lives in ~/bgutil-ytdlp-pot-provider) for PO tokens, and                #
#    * the Deno JS runtime + yt-dlp's EJS solver (remote_components, below)    #
#      for the JS challenges.                                                  #
#  See README.md ("YouTube setup") for the one-time install of Deno and the    #
#  PO-token provider.                                                          #
# --------------------------------------------------------------------------- #
def ensure_deno():
    """Make the Deno binary discoverable on PATH so yt-dlp can solve YouTube's
    JS challenges. Deno is often installed but only added to a *future* shell's
    PATH (e.g. by winget), so we also probe its known install locations and
    prepend the right directory to PATH for this process."""
    import glob

    if shutil.which("deno"):
        return True
    candidates = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(os.path.join(local, "Microsoft", "WinGet", "Links", "deno.exe"))
        candidates += glob.glob(
            os.path.join(local, "Microsoft", "WinGet", "Packages", "DenoLand.Deno*", "deno.exe")
        )
    candidates.append(os.path.join(os.path.expanduser("~"), ".deno", "bin", "deno.exe"))
    for exe in candidates:
        if os.path.isfile(exe):
            os.environ["PATH"] = os.path.dirname(exe) + os.pathsep + os.environ.get("PATH", "")
            return True
    return False


DENO_OK = ensure_deno()
POT_SERVER_DIR = os.path.join(os.path.expanduser("~"), "bgutil-ytdlp-pot-provider", "server")
POT_OK = os.path.isdir(os.path.join(POT_SERVER_DIR, "build"))


def apply_youtube_solvers(opts):
    """Opt in to the EJS challenge-solver download that Deno needs to solve
    YouTube's JS / n-sig challenges. (The PO-token provider is auto-loaded as a
    yt-dlp plugin and needs no options here.) Harmless for non-YouTube URLs."""
    opts.setdefault("remote_components", set()).add("ejs:github")


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #
def sse(obj):
    """Format a dict as a Server-Sent-Events data frame."""
    return f"data: {json.dumps(obj)}\n\n"


def push(job_id, event):
    """Send a progress event to a job's listeners."""
    job = JOBS.get(job_id)
    if not job:
        return
    if event.get("status") in ("complete", "error"):
        job["finished"] = True
    job["q"].put(event)


def detect_platform(url):
    """Classify a URL into one of the platforms we surface in the UI."""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if "youtube" in host or host == "youtu.be":
        return "youtube"
    if "facebook" in host or host == "fb.watch" or host == "fb.com":
        return "facebook"
    if "instagram" in host:
        return "instagram"
    if "tiktok" in host:
        return "tiktok"
    if host in ("x.com", "twitter.com") or host.endswith((".x.com", ".twitter.com")):
        return "x"
    return "other"


def url_slug(url):
    """A short, filesystem-friendly tag derived from the URL (for folder names)."""
    skip = {"p", "reel", "reels", "video", "status", "photo", "tv", "watch", "v"}
    parts = [seg for seg in urlparse(url).path.strip("/").split("/") if seg and seg.lower() not in skip]
    slug = parts[-1] if parts else ""
    return "".join(c for c in slug if c.isalnum() or c in "-_")[:40]


def clean_cookies(value):
    """Return a valid browser name for cookie extraction, or None."""
    value = (value or "").strip().lower()
    return value if value in ALLOWED_BROWSERS else None


def clean_cookiefile(path):
    """Return an absolute path to an existing cookies.txt file, or None."""
    path = (path or "").strip()
    if not path:
        return None
    path = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    return path if os.path.isfile(path) else None


def auto_cookiefile():
    """Path to a cookies.txt placed next to app.py, if present (used by default)."""
    path = os.path.join(APP_DIR, "cookies.txt")
    return path if os.path.isfile(path) else None


def resolve_login(params):
    """Decide the login source: explicit file > chosen browser > app cookies.txt.

    Returns (cookiefile_path, browser_name); at most one is set.
    """
    cookiefile = clean_cookiefile(params.get("cookiefile"))
    if cookiefile:
        return cookiefile, None
    browser = clean_cookies(params.get("cookies"))
    if browser:
        return None, browser
    return auto_cookiefile(), None


def apply_cookies(opts, params):
    """Attach login cookies to a yt-dlp options dict."""
    cookiefile, browser = resolve_login(params)
    if cookiefile:
        opts["cookiefile"] = cookiefile
    elif browser:
        opts["cookiesfrombrowser"] = (browser,)


def fmt_meta(info):
    """Pull the small, JSON-safe bits we want to show for a single video."""
    heights = sorted(
        {f.get("height") for f in info.get("formats", []) if f.get("height")},
        reverse=True,
    )
    return {
        "type": "video",
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "max_height": heights[0] if heights else None,
        "heights": heights,
        "has_subs": bool(info.get("subtitles") or info.get("automatic_captions")),
    }


def resolve_dest(dest):
    """Turn a user-supplied folder string into an absolute path (default if blank)."""
    if not dest or not str(dest).strip():
        return DOWNLOAD_DIR
    expanded = os.path.expanduser(os.path.expandvars(str(dest).strip()))
    return os.path.abspath(expanded)


def within_known(target):
    """True if `target` lives inside any folder we've downloaded into."""
    t = os.path.normcase(os.path.realpath(target))
    for base in list(KNOWN_DESTS):
        b = os.path.normcase(os.path.realpath(base))
        try:
            if os.path.commonpath([b, t]) == b:
                return True
        except ValueError:  # different drives on Windows
            continue
    return False


def pick_folder(initial):
    """Show a native folder picker on the server machine; return the path or None.

    On Windows we shell out to a PowerShell FolderBrowserDialog so the GUI runs
    in its own process — far more robust than driving a toolkit from inside
    Flask's worker threads. Elsewhere we fall back to tkinter.
    """
    if os.name == "nt":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$o = New-Object System.Windows.Forms.Form; $o.TopMost = $true;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            '$d.Description = "Choose a download folder";'
            "if ($env:YTDL_INITDIR -and (Test-Path $env:YTDL_INITDIR)) "
            "{ $d.SelectedPath = $env:YTDL_INITDIR };"
            "if ($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Out.Write($d.SelectedPath) }; $o.Dispose()"
        )
        env = dict(os.environ, YTDL_INITDIR=initial or "")
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True,
            text=True,
            env=env,
        )
        return (proc.stdout or "").strip() or None

    if TK_AVAILABLE:
        result = {}

        def run_dialog():
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            start = initial if initial and os.path.isdir(initial) else DOWNLOAD_DIR
            result["path"] = filedialog.askdirectory(
                initialdir=start, title="Choose a download folder"
            )
            root.destroy()

        worker = threading.Thread(target=run_dialog)
        worker.start()
        worker.join()
        return result.get("path") or None

    return None


def pick_file():
    """Show a native file picker for a cookies.txt file; return the path or None."""
    if os.name == "nt":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$o = New-Object System.Windows.Forms.Form; $o.TopMost = $true;"
            "$d = New-Object System.Windows.Forms.OpenFileDialog;"
            '$d.Title = "Select your cookies.txt file";'
            '$d.Filter = "Cookies file (*.txt)|*.txt|All files (*.*)|*.*";'
            "if ($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Out.Write($d.FileName) }; $o.Dispose()"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True,
            text=True,
        )
        return (proc.stdout or "").strip() or None

    if TK_AVAILABLE:
        result = {}

        def run_dialog():
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            result["path"] = filedialog.askopenfilename(
                title="Select your cookies.txt file",
                filetypes=[("Cookies file", "*.txt"), ("All files", "*.*")],
            )
            root.destroy()

        worker = threading.Thread(target=run_dialog)
        worker.start()
        worker.join()
        return result.get("path") or None

    return None


# --------------------------------------------------------------------------- #
#  Engine 1: yt-dlp (video / audio)                                           #
# --------------------------------------------------------------------------- #
def build_opts(job_id, params, base_dir):
    content = (params.get("content") or "video").lower()  # "video" | "audio"
    quality = str(params.get("quality", "best"))          # "best" | "2160" | "1080" ...
    want_subs = bool(params.get("subs"))
    sub_langs = params.get("subLangs") or "en"
    want_playlist = bool(params.get("playlist"))

    if want_playlist:
        outtmpl = os.path.join(
            base_dir,
            "%(playlist_title).80B",
            "%(playlist_index)03d - %(title).120B [%(id)s].%(ext)s",
        )
    else:
        outtmpl = os.path.join(base_dir, "%(title).150B [%(id)s].%(ext)s")

    def progress_hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            info = d.get("info_dict") or {}
            push(
                job_id,
                {
                    "status": "downloading",
                    "percent": round(done / total * 100, 1) if total else 0,
                    "downloaded": done,
                    "total": total,
                    "speed": d.get("speed") or 0,
                    "eta": d.get("eta") or 0,
                    "item": info.get("playlist_index"),
                    "item_count": info.get("n_entries"),
                    "title": info.get("title"),
                },
            )
        elif status == "finished":
            push(job_id, {"status": "processing", "stage": "Processing"})

    def pp_hook(d):
        if d.get("status") == "started":
            name = (d.get("postprocessor") or "").replace("FFmpeg", "")
            label = {
                "ExtractAudio": "Converting to MP3",
                "Merger": "Merging video + audio",
                "EmbedSubtitle": "Embedding subtitles",
                "VideoConvertor": "Converting",
            }.get(name, "Processing")
            push(job_id, {"status": "processing", "stage": label})

    opts = {
        "outtmpl": outtmpl,
        "noplaylist": not want_playlist,
        "ignoreerrors": want_playlist,      # skip broken items in a playlist
        "windowsfilenames": True,
        "concurrent_fragment_downloads": 4,
        "retries": 5,
        "fragment_retries": 5,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [pp_hook],
        "postprocessors": [],
    }
    if FFMPEG_DIR:
        opts["ffmpeg_location"] = FFMPEG_DIR
    apply_cookies(opts, params)
    apply_youtube_solvers(opts)

    if content == "audio":
        opts["format"] = "ba/b"
        opts["postprocessors"].append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        )
    else:
        if quality == "best":
            opts["format"] = "bv*+ba/b"
        else:
            h = int(quality)
            opts["format"] = f"bv*[height<={h}]+ba/b[height<={h}]/b[height<={h}]/b"
        opts["merge_output_format"] = "mp4"

    if want_subs:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = [s.strip() for s in sub_langs.split(",") if s.strip()] or ["en"]
        opts["subtitlesformat"] = "srt/best"
        if content == "video":
            opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle"})

    return opts


def run_ytdlp(job_id, params, base_dir):
    job = JOBS[job_id]
    try:
        opts = build_opts(job_id, params, base_dir)
        with YoutubeDL(opts) as ydl:
            ydl.add_post_hook(lambda fn: job["files"].append(fn))
            ydl.download([params["url"]])
        files, seen = [], set()
        for f in job["files"]:
            full = os.path.abspath(f)
            if full not in seen:
                seen.add(full)
                files.append(full)
        files.sort()
        push(
            job_id,
            {"status": "complete", "files": files, "dest": base_dir, "count": len(files)},
        )
    except Exception as exc:  # noqa: BLE001
        push(job_id, {"status": "error", "message": str(exc)})


# --------------------------------------------------------------------------- #
#  Engine 2: gallery-dl (images / mixed media)                                #
# --------------------------------------------------------------------------- #
MEDIA_EXTS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic",
    ".mp4", ".webm", ".mov", ".m4v", ".mkv", ".m4a", ".mp3",
)

# Run the gallery-dl subprocess through a bootstrap so it also verifies TLS
# against the OS trust store (same reason as the truststore note up top).
GDL_BOOT = (
    "import runpy\n"
    "try:\n"
    "    import truststore; truststore.inject_into_ssl()\n"
    "except Exception:\n"
    "    pass\n"
    "runpy.run_module('gallery_dl', run_name='__main__')\n"
)


def run_gallerydl(job_id, url, base_dir, platform, cookies, cookiefile):
    job = JOBS[job_id]
    slug = url_slug(url) or job_id[:8]
    sub = os.path.join(base_dir, f"{platform}_{slug}")
    os.makedirs(sub, exist_ok=True)

    cmd = [sys.executable, "-c", GDL_BOOT, "-D", sub, url]
    if cookiefile:
        cmd += ["--cookies", cookiefile]
    elif cookies:
        cmd += ["--cookies-from-browser", cookies]

    push(job_id, {"status": "processing", "stage": "Fetching media"})

    stderr_lines = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:  # noqa: BLE001
        push(job_id, {"status": "error", "message": str(exc)})
        return

    def drain_err():
        for ln in proc.stderr:
            stderr_lines.append(ln.rstrip())

    err_thread = threading.Thread(target=drain_err, daemon=True)
    err_thread.start()

    count = 0
    for line in proc.stdout:
        raw = line.strip()
        if raw.startswith(("# ", "* ", "| ")):
            raw = raw[2:].strip()
        if raw.lower().endswith(MEDIA_EXTS):
            count += 1
            push(job_id, {"status": "images", "count": count, "latest": os.path.basename(raw)})
    proc.wait()
    err_thread.join(timeout=2)

    files = []
    for root, _dirs, names in os.walk(sub):
        for name in names:
            files.append(os.path.abspath(os.path.join(root, name)))
    files.sort()

    if files:
        push(
            job_id,
            {"status": "complete", "files": files, "dest": sub, "count": len(files)},
        )
        return

    # Nothing downloaded — surface a helpful reason.
    try:
        os.rmdir(sub)  # remove the empty folder we created
    except OSError:
        pass
    tail = " ".join(stderr_lines[-3:]).strip()
    message = tail or "No downloadable media found at that URL."
    blob = " ".join(stderr_lines).lower()
    if any(k in blob for k in ("login", "authoriz", "403", "private", "age", "consent", "redirect to login")):
        message = (
            "This post requires being logged in. Choose “Cookies.txt file…” under "
            "“Use login from” and pick a cookies file exported from a browser where "
            "you're signed in. (See the README — browser cookie reading often fails "
            "on Windows for Chrome/Edge.)"
        )
    push(job_id, {"status": "error", "message": message})


# --------------------------------------------------------------------------- #
#  Routes                                                                      #
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template(
        "index.html",
        ffmpeg_ok=bool(FFMPEG_DIR),
        gallerydl_ok=GALLERYDL_OK,
        default_dir=DOWNLOAD_DIR,
        browse_available=BROWSE_AVAILABLE,
        auto_cookies=bool(auto_cookiefile()),
    )


@app.route("/api/info", methods=["POST"])
def api_info():
    body = request.json or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Please paste a link."}), 400
    platform = detect_platform(url)

    if platform == "youtube":
        try:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": "in_playlist",
                "noplaylist": False,
                # Only pull the first dozen items for the preview. Without this a
                # large playlist (hundreds/thousands of videos) would page through
                # the entire list before we could show anything — the "Fetch"
                # spinner appears to hang. The full list is enumerated later, at
                # download time. The true length still comes back as
                # `playlist_count`, so the count stays accurate.
                "playlist_items": "1-12",
            }
            apply_cookies(opts, body)
            apply_youtube_solvers(opts)
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if info.get("_type") == "playlist" or "entries" in info:
                entries = [e for e in (info.get("entries") or []) if e]
                preview = [
                    {
                        "title": e.get("title"),
                        "id": e.get("id"),
                        "thumbnail": (e.get("thumbnails") or [{}])[-1].get("url"),
                        "duration": e.get("duration"),
                    }
                    for e in entries[:12]
                ]
                return jsonify(
                    {
                        "type": "playlist",
                        "platform": "youtube",
                        "title": info.get("title"),
                        "uploader": info.get("uploader") or info.get("channel"),
                        "count": info.get("playlist_count") or len(entries),
                        "entries": preview,
                    }
                )
            data = fmt_meta(info)
            data["platform"] = "youtube"
            return jsonify(data)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400

    # Non-YouTube: best-effort probe via yt-dlp. Failures are fine — many image
    # posts simply aren't videos, so we fall back to "media" and let the user
    # grab images with gallery-dl.
    meta = {
        "type": "media",
        "platform": platform,
        "title": None,
        "uploader": None,
        "thumbnail": None,
        "duration": None,
        "max_height": None,
        "has_subs": False,
    }
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
        apply_cookies(opts, body)
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info:
            heights = sorted(
                {f.get("height") for f in info.get("formats", []) if f.get("height")},
                reverse=True,
            )
            has_video = bool(heights) or (info.get("vcodec") not in (None, "none"))
            meta.update(
                type="video" if has_video else "media",
                title=info.get("title"),
                uploader=info.get("uploader") or info.get("channel"),
                thumbnail=info.get("thumbnail"),
                duration=info.get("duration"),
                max_height=heights[0] if heights else None,
            )
    except Exception:  # noqa: BLE001
        pass
    return jsonify(meta)


@app.route("/api/download", methods=["POST"])
def api_download():
    params = request.json or {}
    url = (params.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing URL."}), 400

    base_dir = resolve_dest(params.get("dest"))
    try:
        os.makedirs(base_dir, exist_ok=True)
        probe = os.path.join(base_dir, ".yt_write_test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Can't save to that folder: {exc}"}), 400
    KNOWN_DESTS.add(os.path.realpath(base_dir))

    content = (params.get("content") or "video").lower()
    platform = detect_platform(url)
    cookiefile, cookies = resolve_login(params)

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"q": queue.Queue(), "files": [], "finished": False}

    if content in ("images", "media"):
        if not GALLERYDL_OK:
            return jsonify({"error": "gallery-dl is not installed."}), 400
        target = lambda: run_gallerydl(job_id, url, base_dir, platform, cookies, cookiefile)  # noqa: E731
    else:
        target = lambda: run_ytdlp(job_id, params, base_dir)  # noqa: E731

    threading.Thread(target=target, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def api_progress(job_id):
    def gen():
        job = JOBS.get(job_id)
        if not job:
            yield sse({"status": "error", "message": "Unknown job."})
            return
        while True:
            try:
                event = job["q"].get(timeout=15)
            except queue.Empty:
                yield ": ping\n\n"
                if job.get("finished"):
                    break
                continue
            yield sse(event)
            if event.get("status") in ("complete", "error"):
                break

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/browse-folder", methods=["POST"])
def api_browse_folder():
    """Open a native folder picker on the server machine and return the choice."""
    if not BROWSE_AVAILABLE:
        return jsonify({"unsupported": True}), 200
    initial = resolve_dest((request.json or {}).get("current"))
    if not _dialog_lock.acquire(blocking=False):
        return jsonify({"error": "A folder dialog is already open."}), 409
    try:
        chosen = pick_folder(initial)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    finally:
        _dialog_lock.release()

    if chosen:
        return jsonify({"path": os.path.normpath(chosen)})
    return jsonify({"cancelled": True})


@app.route("/api/browse-file", methods=["POST"])
def api_browse_file():
    """Open a native file picker (for a cookies.txt file) and return the choice."""
    if not BROWSE_AVAILABLE:
        return jsonify({"unsupported": True}), 200
    if not _dialog_lock.acquire(blocking=False):
        return jsonify({"error": "A dialog is already open."}), 409
    try:
        chosen = pick_file()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    finally:
        _dialog_lock.release()

    if chosen:
        return jsonify({"path": os.path.normpath(chosen)})
    return jsonify({"cancelled": True})


@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    requested = resolve_dest((request.json or {}).get("dest"))
    folder = requested if os.path.isdir(requested) else DOWNLOAD_DIR
    try:
        if os.name == "nt":
            os.startfile(folder)  # noqa: S606
        elif shutil.which("open"):
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/reveal", methods=["POST"])
def api_reveal():
    """Open the OS file manager with a downloaded file highlighted.

    The path must live inside a folder we've downloaded into this session, so a
    crafted request can't reveal arbitrary files on disk.
    """
    path = ((request.json or {}).get("path") or "").strip()
    if not path:
        return jsonify({"error": "No file specified."}), 400

    target = os.path.realpath(path)
    if not within_known(target):
        return jsonify({"error": "Invalid path."}), 400
    if not os.path.exists(target):
        return jsonify({"error": "File no longer exists."}), 404

    try:
        if os.name == "nt":
            # Quoted comma-form is the invocation Explorer reliably parses.
            subprocess.Popen('explorer /select,"%s"' % target)
        elif shutil.which("open"):
            subprocess.Popen(["open", "-R", target])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(target)])
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


def open_browser():
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()


def free_port(port):
    """Terminate any previous instance still listening on `port` before we bind.

    Windows allows several processes to share a port via SO_REUSEADDR, and the
    *oldest* one keeps answering — so relaunching the app would silently keep
    serving stale code. Clearing the port first guarantees the newest launch
    wins, which is what "restart the app" should mean.
    """
    import socket
    import time

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return  # nothing is listening — clean start

    pids = set()
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                    pids.add(parts[4])
            for pid in pids:
                if pid and pid != "0":
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        else:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
            ).stdout
            for pid in out.split():
                pids.add(pid)
                subprocess.run(["kill", "-9", pid], capture_output=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Could not free port {port}: {exc}")
        return

    if pids:
        print(f"  (stopped a previous instance already on port {port})")
        time.sleep(0.8)  # let the OS release the socket before we bind


if __name__ == "__main__":
    free_port(PORT)
    print("=" * 60)
    print("  Media Downloader")
    print(f"  Default downloads: {DOWNLOAD_DIR}")
    print(f"  ffmpeg:     {'ok' if FFMPEG_DIR else 'NOT FOUND'}")
    print(f"  gallery-dl: {'ok' if GALLERYDL_OK else 'NOT INSTALLED (images disabled)'}")
    print(f"  Deno (JS):  {'ok' if DENO_OK else 'NOT FOUND (YouTube may hit bot checks)'}")
    print(f"  PO token:   {'ok' if POT_OK else 'NOT SET UP (YouTube may hit bot checks)'}")
    print(f"  Open: http://127.0.0.1:{PORT}")
    print("=" * 60)
    open_browser()
    app.run(host="127.0.0.1", port=PORT, threaded=True)
