"use strict";

const $ = (id) => document.getElementById(id);

let state = {
  url: "",
  platform: "other",
  isPlaylist: false,
  count: 0,
  mode: "video",      // "video" | "images" — auto-detected, optionally overridden
  overridden: false,
  cookiefile: "",     // path to a chosen cookies.txt file
};

let lastDest = "";
const DEST_KEY = "ytdl_dest";
const COOKIES_KEY = "ytdl_cookies";
const COOKIEFILE_KEY = "ytdl_cookiefile";
const currentDest = () => $("dest").value.trim();

// Login params sent with info/download requests (a cookies.txt file wins).
function cookieParams() {
  const sel = $("cookies").value;
  const browser = (sel === "file" || sel === "") ? "" : sel;
  return { cookies: browser, cookiefile: sel === "file" ? state.cookiefile : "" };
}

const PLATFORM_NAMES = {
  youtube: "YouTube",
  facebook: "Facebook",
  instagram: "Instagram",
  tiktok: "TikTok",
  x: "X",
  other: "Link",
};

// Honest, per-platform guidance shown under the Download button.
const HINTS = {
  instagram: "Instagram requires login. On Windows use “Cookies.txt file…” — reading Chrome/Edge cookies usually fails. See the README to export one.",
  facebook: "Facebook usually needs login and is hit-or-miss. “Cookies.txt file…” works best.",
  x: "Some posts require login. Mixed photo/video posts grab everything.",
  tiktok: "TikTok videos and photo posts are both supported.",
};

// ---- helpers ---------------------------------------------------------------
function fmtDuration(s) {
  if (!s) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function fmtBytes(b) {
  if (!b) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(1)} ${u[i]}`;
}

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

// ---- fetch info ------------------------------------------------------------
async function fetchInfo() {
  const url = $("url").value.trim();
  if (!url) return;
  state.url = url;

  const msg = $("fetchMsg");
  msg.className = "msg loading";
  msg.textContent = "Detecting…";
  $("fetch").disabled = true;
  hide($("preview")); hide($("progress")); hide($("done"));

  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, ...cookieParams() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not read that link.");

    msg.textContent = "";
    renderPreview(data);
  } catch (e) {
    msg.className = "msg err";
    msg.textContent = e.message;
  } finally {
    $("fetch").disabled = false;
  }
}

function renderPreview(data) {
  state.platform = data.platform || "other";
  state.overridden = false;
  const isYt = state.platform === "youtube";
  const pretty = PLATFORM_NAMES[state.platform] || "Link";
  const badge = $("pvBadge");

  if (data.type === "playlist") {
    state.isPlaylist = true;
    state.count = data.count || 0;
    state.mode = "video";
    $("thumb").src = (data.entries[0] && data.entries[0].thumbnail) || "";
    show($("thumb"));
    $("pvTitle").textContent = data.title || "Playlist";
    $("pvMeta").textContent = `${data.uploader || ""} · ${data.count} videos`.trim();
    badge.textContent = `${pretty} playlist`;
    show(badge);
    $("playlist").checked = true;
    buildQualityOptions(null);
  } else {
    state.isPlaylist = false;
    // Auto-detect: a video where one was found, otherwise an image/media post
    state.mode = data.type === "video" ? "video" : "images";
    if (data.thumbnail) {
      $("thumb").src = data.thumbnail;
      show($("thumb"));
    } else {
      hide($("thumb"));
    }
    $("pvTitle").textContent = data.title || `${pretty} post`;
    const parts = [];
    if (data.uploader) parts.push(data.uploader);
    if (data.duration) parts.push(fmtDuration(data.duration));
    if (data.max_height) parts.push(`up to ${data.max_height}p`);
    $("pvMeta").textContent = parts.join(" · ");
    badge.textContent = pretty;
    show(badge);
    $("playlist").checked = false;
    $("audioOnly").checked = false;
    buildQualityOptions(data.max_height);
  }

  const hint = HINTS[state.platform];
  if (hint && !isYt) { $("hint").textContent = hint; show($("hint")); }
  else hide($("hint"));

  applyMode();
  show($("preview"));
}

function applyMode() {
  const isYt = state.platform === "youtube";
  const isVideo = state.mode === "video";

  let nice;
  if (state.isPlaylist) nice = `Playlist · ${state.count} videos`;
  else if (isVideo) nice = "Video";
  else nice = "Images & media";
  $("detectedLabel").textContent = (state.overridden ? "Downloading: " : "Auto-detected: ") + nice;

  $("qualityOpt").classList.toggle("hidden", !isVideo);
  $("audioOpt").classList.toggle("hidden", !isVideo);
  $("playlistOpt").classList.toggle("hidden", !state.isPlaylist);
  $("subsOpt").classList.toggle("hidden", !(isYt && isVideo));
  $("cookiesOpt").classList.toggle("hidden", isYt);

  // Let the user override the guess on social posts (e.g. mixed carousels)
  const sw = $("switchMode");
  if (!isYt && !state.isPlaylist) {
    sw.textContent = isVideo ? "Not a video? Get images instead" : "Get the video instead";
    show(sw);
  } else {
    hide(sw);
  }
}

function buildQualityOptions(maxHeight) {
  const sel = $("quality");
  sel.innerHTML = "";
  const buckets = [
    ["best", "Best available"],
    ["2160", "4K · 2160p"],
    ["1440", "1440p"],
    ["1080", "1080p"],
    ["720", "720p"],
    ["480", "480p"],
    ["360", "360p"],
  ];
  for (const [val, label] of buckets) {
    if (maxHeight && val !== "best" && Number(val) > maxHeight) continue;
    const o = document.createElement("option");
    o.value = val;
    o.textContent = label;
    sel.appendChild(o);
  }
  sel.value = "best";
}

// ---- download --------------------------------------------------------------
function chosenContent() {
  if (state.mode !== "video") return "images";
  return $("audioOnly").checked ? "audio" : "video";
}

async function startDownload() {
  const content = chosenContent();
  const params = {
    url: state.url,
    content: content,
    quality: $("quality").value,
    subs: $("subs").checked,
    subLangs: $("subLangs").value,
    playlist: state.isPlaylist && $("playlist").checked,
    ...cookieParams(),
    dest: currentDest(),
  };

  $("download").disabled = true;
  $("fetch").disabled = true;
  hide($("done"));
  show($("progress"));
  $("progStage").textContent = "Starting…";
  $("progItem").textContent = "";
  $("progStats").textContent = "";
  $("progPct").textContent = content === "images" ? "" : "0%";
  $("barFill").style.width = "0%";
  $("barFill").classList.toggle("indeterminate", content === "images");

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not start download.");
    listenProgress(data.job_id);
  } catch (e) {
    $("progStage").textContent = "Error: " + e.message;
    $("barFill").classList.remove("indeterminate");
    $("download").disabled = false;
    $("fetch").disabled = false;
  }
}

function listenProgress(jobId) {
  const es = new EventSource(`/api/progress/${jobId}`);

  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);

    if (d.status === "downloading") {
      $("barFill").classList.remove("indeterminate");
      $("progStage").textContent = chosenContent() === "audio" ? "Downloading audio" : "Downloading";
      $("barFill").style.width = d.percent + "%";
      $("progPct").textContent = d.percent + "%";
      if (d.item && d.item_count) $("progItem").textContent = `Item ${d.item} of ${d.item_count}`;
      const stats = [];
      if (d.total) stats.push(`${fmtBytes(d.downloaded)} / ${fmtBytes(d.total)}`);
      if (d.speed) stats.push(`${fmtBytes(d.speed)}/s`);
      if (d.eta) stats.push(`ETA ${fmtDuration(d.eta)}`);
      $("progStats").textContent = stats.join("  ·  ");
    } else if (d.status === "images") {
      $("barFill").classList.add("indeterminate");
      $("progStage").textContent = "Downloading media";
      $("progPct").textContent = d.count + (d.count === 1 ? " file" : " files");
      $("progStats").textContent = d.latest ? "Latest: " + d.latest : "";
    } else if (d.status === "processing") {
      $("progStage").textContent = (d.stage || "Processing") + "…";
      $("progStats").textContent = "";
    } else if (d.status === "complete") {
      es.close();
      finishUp(d);
    } else if (d.status === "error") {
      es.close();
      $("barFill").classList.remove("indeterminate");
      $("progStage").textContent = "Error";
      $("progStats").textContent = d.message || "";
      $("download").disabled = false;
      $("fetch").disabled = false;
    }
  };

  es.onerror = () => {
    es.close();
    $("download").disabled = false;
    $("fetch").disabled = false;
  };
}

function finishUp(d) {
  hide($("progress"));
  $("barFill").classList.remove("indeterminate");
  lastDest = d.dest || currentDest();
  $("savedTo").textContent = lastDest ? "Saved to " + lastDest : "";
  const ul = $("fileList");
  ul.innerHTML = "";
  (d.files || []).forEach((full) => {
    const li = document.createElement("li");

    const span = document.createElement("span");
    span.className = "fname";
    span.textContent = full.split(/[\\/]/).pop();
    span.title = full;

    const btn = document.createElement("button");
    btn.className = "reveal";
    btn.textContent = "Reveal";
    btn.title = "Show this file in Explorer";
    btn.addEventListener("click", () => revealFile(full, btn));

    li.appendChild(span);
    li.appendChild(btn);
    ul.appendChild(li);
  });
  if (!d.files || !d.files.length) {
    const li = document.createElement("li");
    li.textContent = `${d.count || 0} file(s) saved.`;
    ul.appendChild(li);
  }
  show($("done"));
  $("download").disabled = false;
  $("fetch").disabled = false;
}

async function revealFile(path, btn) {
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.error || "Could not reveal the file.");
    }
  } catch (e) {
    alert("Could not reveal the file.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function browseFolder() {
  const btn = $("browse");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/browse-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current: currentDest() }),
    });
    const data = await res.json();
    if (data.path) {
      $("dest").value = data.path;
      localStorage.setItem(DEST_KEY, data.path);
    } else if (data.unsupported) {
      alert("Native folder picker isn't available here — type or paste a path instead.");
    }
  } catch (e) {
    alert("Could not open the folder picker.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function openFolder(dest) {
  try {
    await fetch("/api/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dest: dest || currentDest() }),
    });
  } catch (e) {}
}

function setCookieFile(path) {
  state.cookiefile = path || "";
  const opt = $("cookies").querySelector('option[value="file"]');
  if (path) {
    opt.textContent = "📄 " + path.split(/[\\/]/).pop();
    $("cookies").value = "file";
    localStorage.setItem(COOKIEFILE_KEY, path);
  } else {
    opt.textContent = "Cookies.txt file…";
    localStorage.removeItem(COOKIEFILE_KEY);
  }
}

async function browseCookieFile() {
  try {
    const res = await fetch("/api/browse-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (data.path) { setCookieFile(data.path); return true; }
    if (data.unsupported) alert("File picker isn't available here.");
    return false;
  } catch (e) {
    alert("Could not open the file picker.");
    return false;
  }
}

// ---- wire up ---------------------------------------------------------------
// Restore the saved destination folder (falls back to the server default)
(function init() {
  const saved = localStorage.getItem(DEST_KEY);
  if (saved) $("dest").value = saved;
  else localStorage.setItem(DEST_KEY, currentDest());
  const cf = localStorage.getItem(COOKIEFILE_KEY);
  if (cf) {
    setCookieFile(cf);
  } else {
    const ck = localStorage.getItem(COOKIES_KEY);
    if (ck) $("cookies").value = ck;
  }
})();

$("fetch").addEventListener("click", fetchInfo);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") fetchInfo(); });
$("download").addEventListener("click", startDownload);
$("dest").addEventListener("input", () => localStorage.setItem(DEST_KEY, currentDest()));
$("cookies").addEventListener("change", async () => {
  const v = $("cookies").value;
  if (v === "file") {
    const ok = await browseCookieFile();
    if (!ok) $("cookies").value = state.cookiefile ? "file" : "";
  } else {
    setCookieFile("");
    localStorage.setItem(COOKIES_KEY, v);
  }
});
$("switchMode").addEventListener("click", (e) => {
  e.preventDefault();
  state.mode = state.mode === "video" ? "images" : "video";
  state.overridden = true;
  applyMode();
});
const browseBtn = $("browse");
if (browseBtn) browseBtn.addEventListener("click", browseFolder);
$("openFolder").addEventListener("click", () => openFolder(currentDest()));
$("openFolder2").addEventListener("click", () => openFolder(lastDest));
