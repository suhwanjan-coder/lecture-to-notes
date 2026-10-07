"""Step 0 of the lecture runner: get the media (and any YouTube subtitles) ready.

Usage:
    python scripts/fetch_media.py <url-or-path> --out <lecture_dir>
        [--max-height 1080] [--subs zh-Hant,zh-TW,zh,en] [--section START-END]

Modes
  local file    ffprobe must report an audio stream; the absolute path is the
                media. NOTHING is copied (lecture videos are GBs).
  local folder  lists the media / pdf / pptx files found and exits 0 with a JSON
                summary. Never picks a video for the user.
  URL           yt-dlp -> <out>/source/<name>.mp4 (best video <= max-height +
                best audio, merged to mp4), plus subtitles (manual first, then
                auto-generated) converted to <out>/source/youtube_subs.json
                ([{start,end,text}], same shape as transcript.json) and
                <out>/source/source.json (url/title/uploader/date/duration/
                chapters/description/which subtitle track).
                youtube_subs.json is an EXTRA reference for the note writer,
                never the primary transcript.

--section START-END passes through to yt-dlp's download-sections (seconds,
MM:SS or HH:MM:SS; for tests / partial downloads). Subtitle times are shifted so
they line up with the partial media (t=0 is START).

The LAST stdout line is always one JSON object:
    {"media": <path|null>, "subs": <path|null>, "meta": <path|null>}
(folder mode adds "folder"/"found"/"counts" keys). Exit 0 = ok, 1 = failure,
2 = bad input. Progress/logging goes to stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

try:  # TLS-intercepting machines: use the OS trust store (never verify=False)
    import truststore  # type: ignore
    truststore.inject_into_ssl()
except ImportError:
    pass

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def final(media, subs=None, meta=None, **extra) -> None:
    out = {"media": media, "subs": subs, "meta": meta}
    out.update(extra)
    print(json.dumps(out, ensure_ascii=False), flush=True)


def fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------- helpers

def parse_time(s: str) -> float:
    s = s.strip()
    parts = s.split(":")
    if not s or len(parts) > 3:
        raise ValueError(s)
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec


def parse_section(spec: str) -> tuple[float, float]:
    spec = spec.strip().lstrip("*")
    m = re.fullmatch(r"([\d:.]+)\s*-\s*([\d:.]+)", spec)
    if not m:
        fail(f"--section must look like START-END (e.g. 60-150 or 1:00-2:30), got {spec!r}", 2)
    try:
        a, b = parse_time(m.group(1)), parse_time(m.group(2))
    except ValueError:
        fail(f"--section: cannot parse times in {spec!r}", 2)
    if b <= a:
        fail(f"--section: END must be greater than START ({spec!r})", 2)
    return a, b


def safe_name(title: str, vid: str, maxlen: int = 40) -> str:
    """Windows-path-safe, ffmpeg-safe short name; keeps CJK, drops punctuation
    that breaks shells/filter graphs (quotes, brackets, %, &, :, etc.)."""
    t = re.sub(r"[^\w一-鿿㐀-䶿-]+", "_", title or "", flags=re.UNICODE)
    t = re.sub(r"_+", "_", t).strip("_-")[:maxlen].strip("_-")
    vid = re.sub(r"[^\w-]", "", vid or "")
    return f"{t}_{vid}" if t and vid else (t or vid or "media")


def ffprobe_streams(path: str) -> list[dict]:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name:format=duration", "-of", "json", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        fail("ffprobe not found on PATH")
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout).get("streams", [])
    except json.JSONDecodeError:
        return []


def write_json_atomic(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- local modes

def do_local_file(path: str) -> None:
    ap = os.path.abspath(path)
    streams = ffprobe_streams(ap)
    if not streams:
        fail(f"ffprobe cannot read {ap} (not a media file?)", 2)
    if not any(s.get("codec_type") == "audio" for s in streams):
        fail(f"no audio stream in {ap}; nothing to transcribe", 2)
    kinds = sorted({s.get("codec_type") for s in streams if s.get("codec_type")})
    log(f"local media OK ({'+'.join(kinds)}): {ap}")
    final(ap)


def do_local_folder(folder: str) -> None:
    ap = os.path.abspath(folder)
    found = {"video": [], "audio": [], "pdf": [], "pptx": []}
    for dp, dns, fns in os.walk(ap):
        dns[:] = sorted(d for d in dns if not d.startswith((".", "_")) and d != "影片筆記整合")
        for fn in sorted(fns):
            ext = os.path.splitext(fn)[1].lower()
            full = os.path.join(dp, fn)
            if ext in VIDEO_EXT:
                found["video"].append(full)
            elif ext in AUDIO_EXT:
                found["audio"].append(full)
            elif ext == ".pdf":
                found["pdf"].append(full)
            elif ext in (".pptx", ".ppt"):
                found["pptx"].append(full)
    for k, v in found.items():
        log(f"{k}: {len(v)}")
        for p in v:
            log(f"  {p}")
    if len(found["video"]) + len(found["audio"]) != 1:
        log("zero or several media files; folder mode never chooses. "
            "Re-run with the chosen file path.")
    else:
        log("one media file found, but folder mode never chooses; pass the file path explicitly.")
    final(None, folder=ap, found=found, counts={k: len(v) for k, v in found.items()})


# ---------------------------------------------------------------- subtitles

_TAG = re.compile(r"<[^>]*>")
_TS = re.compile(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)")


def _ts(s: str) -> float:
    m = _TS.fullmatch(s.strip())
    if not m:
        raise ValueError(s)
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_vtt(text: str) -> list[dict]:
    """VTT -> [{start,end,text}]. Handles YouTube auto-caption 'rolling' cues
    (each cue repeats the previous cue's last line) by emitting only new lines."""
    segs: list[dict] = []
    prev_lines: list[str] = []
    for block in re.split(r"\r?\n\r?\n", text.replace("﻿", "")):
        lines = [l for l in block.splitlines() if l.strip()]
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        a, _, b = lines[ti].partition("-->")
        try:
            start, end = _ts(a), _ts(b.split()[0])
        except (ValueError, IndexError):
            continue
        cur = []
        for l in lines[ti + 1:]:
            l = _TAG.sub("", l)
            l = l.replace("&nbsp;", " ").replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
            l = re.sub(r"\s+", " ", l).strip()
            if l:
                cur.append(l)
        new = [l for l in cur if l not in prev_lines]
        prev_lines = cur
        if not new:
            continue
        segs.append({"start": round(start, 3), "end": round(end, 3), "text": " ".join(new)})
    out: list[dict] = []
    for s in segs:
        if out and out[-1]["text"] == s["text"]:
            out[-1]["end"] = max(out[-1]["end"], s["end"])
        else:
            out.append(s)
    return out


def pick_track(info: dict, prefs: list[str]):
    """Manual first (in preference order), then auto. Returns (kind, lang) or None."""
    def match(avail: dict, pref: str):
        if pref in avail:
            return pref
        if "-" not in pref:  # 'en' matches 'en-US'; 'zh' matches 'zh-Hans'
            for k in avail:
                if k.split("-")[0] == pref:
                    return k
        return None
    for kind, key in (("manual", "subtitles"), ("auto", "automatic_captions")):
        avail = {k: v for k, v in (info.get(key) or {}).items() if k != "live_chat" and v}
        for pref in prefs:
            lang = match(avail, pref)
            if lang:
                return kind, lang
    return None


# ---------------------------------------------------------------- URL mode

def do_url(url: str, out: str, max_height: int, subs_arg: str, section: str | None) -> None:
    try:
        import yt_dlp
        from yt_dlp.utils import download_range_func
    except ImportError:
        fail("yt_dlp not importable; run with the repo venv (.venv\\Scripts\\python.exe)")

    prefs = [s.strip() for s in subs_arg.split(",") if s.strip()]
    src = os.path.join(os.path.abspath(out), "source")
    os.makedirs(src, exist_ok=True)

    base = {"quiet": not os.environ.get("FETCH_DEBUG"), "no_warnings": True, "noprogress": True, "noplaylist": True,
            "logtostderr": True}
    log(f"probing {url}")
    try:
        with yt_dlp.YoutubeDL(base) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        fail(f"yt-dlp could not read {url}: {e}")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            fail("playlist is empty")
        info = entries[0]
        log("URL was a playlist; using its first entry only")

    name = safe_name(info.get("title", ""), info.get("id", ""))
    track = pick_track(info, prefs)
    log(f"name={name}  subtitle track: {track or 'none matching ' + ','.join(prefs)}")

    h = int(max_height)
    opts = dict(base)
    opts.update({
        "format": (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/"
                   f"b[height<={h}]/b"),
        "merge_output_format": "mp4",
        "outtmpl": {"default": os.path.join(src, name + ".%(ext)s"),
                    "subtitle": os.path.join(src, name + ".%(ext)s")},
        "windowsfilenames": True,
        "overwrites": False,
        "retries": 5, "fragment_retries": 5,
    })
    if track:
        kind, lang = track
        opts.update({"writesubtitles": kind == "manual",
                     "writeautomaticsub": kind == "auto",
                     "subtitleslangs": [lang], "subtitlesformat": "vtt/best"})
    sec_start, sec_end = 0.0, None
    if section:
        sec_start, sec_end = parse_section(section)
        opts["download_ranges"] = download_range_func([], [(sec_start, sec_end)])
        opts["force_keyframes_at_cuts"] = True
        log(f"section {sec_start:g}-{sec_end:g}s")

    log("downloading ...")
    last_err = None
    for attempt in (1, 2):  # ffmpeg section cuts occasionally die on a stale CDN URL
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([info.get("webpage_url") or url])
            last_err = None
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"download attempt {attempt} failed: {e}")
    if last_err is not None:
        fail(f"download failed: {last_err}")

    media = os.path.join(src, name + ".mp4")
    if not os.path.isfile(media):
        cands = [f for f in os.listdir(src) if f.startswith(name + ".")
                 and os.path.splitext(f)[1].lower() in (VIDEO_EXT | AUDIO_EXT)]
        if not cands:
            fail("download finished but no media file found in " + src)
        media = os.path.join(src, cands[0])
    streams = ffprobe_streams(media)
    if not any(s.get("codec_type") == "audio" for s in streams):
        fail(f"downloaded file has no audio stream: {media}")

    # subtitles -> youtube_subs.json (extra reference only)
    subs_path = None
    used = None
    if track:
        kind, lang = track
        vtts = sorted(f for f in os.listdir(src) if f.startswith(name + ".") and f.endswith(".vtt"))
        vtt = next((f for f in vtts if f == f"{name}.{lang}.vtt"), vtts[0] if vtts else None)
        if vtt:
            with open(os.path.join(src, vtt), "r", encoding="utf-8", errors="replace") as fh:
                segs = parse_vtt(fh.read())
            if section:
                segs = [dict(start=round(max(s["start"], sec_start) - sec_start, 3),
                             end=round(min(s["end"], sec_end) - sec_start, 3), text=s["text"])
                        for s in segs if s["end"] > sec_start and s["start"] < sec_end]
            if segs:
                subs_path = os.path.join(src, "youtube_subs.json")
                write_json_atomic(subs_path, segs)
                used = {"kind": kind, "lang": lang, "file": vtt, "segments": len(segs)}
                log(f"subtitles: {kind}/{lang} -> {len(segs)} segments")
            else:
                log("subtitle file parsed to 0 segments; skipping youtube_subs.json")
        else:
            log("expected subtitle file was not written by yt-dlp")

    meta = {
        "url": url,
        "webpage_url": info.get("webpage_url"),
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "upload_date": info.get("upload_date"),
        "duration": info.get("duration"),
        "chapters": info.get("chapters") or [],
        "description": info.get("description"),
        "subtitle_track": used,
        "subtitle_languages_requested": prefs,
        "manual_subtitle_languages": sorted(info.get("subtitles") or {}),
        "section": section,
        "max_height": h,
        "media_file": os.path.abspath(media),
    }
    meta_path = os.path.join(src, "source.json")
    write_json_atomic(meta_path, meta)
    final(os.path.abspath(media),
          os.path.abspath(subs_path) if subs_path else None,
          os.path.abspath(meta_path))


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch media for the lecture pipeline (step 0).")
    ap.add_argument("source", help="YouTube/http URL, local media file, or folder")
    ap.add_argument("--out", required=True, help="lecture directory (URL mode writes <out>/source/)")
    ap.add_argument("--max-height", type=int, default=1080)
    ap.add_argument("--subs", default="zh-Hant,zh-TW,zh,en")
    ap.add_argument("--section", default=None, help="START-END, e.g. 60-150")
    a = ap.parse_args()

    if re.match(r"^https?://", a.source, re.I):
        do_url(a.source, a.out, a.max_height, a.subs, a.section)
    elif os.path.isdir(a.source):
        do_local_folder(a.source)
    elif os.path.isfile(a.source):
        do_local_file(a.source)
    else:
        fail(f"not a URL, file or folder: {a.source}", 2)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    main()
