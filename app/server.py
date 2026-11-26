#!/usr/bin/env python3
"""
kairokee - step 3: local web player

    python server.py

Then open  http://localhost:8000  on this laptop,
or  http://<laptop-ip>:8000  on your phone (same Wi-Fi). The address is printed on start.
"""
import json
import logging
import os
import re
import statistics
import shutil
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import kairokee as kk

PORT = int(os.environ.get("KAIROKEE_PORT") or 8000)   # the launcher picks a free port
# On Windows, closing a browser tab can print a scary but harmless "connection reset" traceback.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
# Private by default: only this computer can open the app.
# Phone mode (other devices on your Wi-Fi) is switched on with:  python server.py --phone
PHONE_MODE = "--phone" in sys.argv or os.environ.get("KAIROKEE_PHONE") == "1"
ROOT = Path(__file__).parent
OUT = kk.OUT
OUT.mkdir(exist_ok=True)

app = FastAPI(title="kairokee")
jobs = {}
job_lock = threading.Lock()


def start_job(stage, slug, work, on_cancel=None):
    """Run work(job) in the background (one job at a time). Returns the job id for /api/job/<id>."""
    with job_lock:
        if any(j["state"] == "running" for j in jobs.values()):
            raise HTTPException(409, "Another song is still being prepared. Wait for it to finish.")
        for old in [k for k, j in jobs.items() if j["state"] != "running"][:-20]:
            del jobs[old]   # forget old finished jobs
        kk.CANCEL.clear()
        job_id = uuid.uuid4().hex[:8]
        job = {"state": "running", "stage": stage, "detail": "", "error": None, "slug": slug}
        jobs[job_id] = job

    def run():
        try:
            work(job)
            job["state"] = "done"
        except kk.Cancelled:
            if on_cancel:
                try:
                    on_cancel()
                except Exception as e:
                    print(f"[!] Could not clean up after stopping: {e}")
            job.update(state="cancelled", error="Stopped.")
            print("      stopped by the user.")
        except kk.KairokeeError as e:
            job.update(state="error", error=str(e))
        except Exception as e:  # anything unexpected: show it instead of hanging
            job.update(state="error", error=f"{type(e).__name__}: {e}")

    threading.Thread(target=run, daemon=True).start()
    return job_id


# --------------------------------------------------------------------------- this computer and how long things take
TIMINGS_FILE = kk.DATA / "timings.json"
timings_lock = threading.Lock()
device_info = {}
# Seconds of waiting per minute of song. Used until this computer has measured its own speed.
DEFAULT_PER_MINUTE = {
    "cuda": {"fast": 15, "split": 30, "transcribe": 20},
    "mps": {"fast": 25, "split": 90, "transcribe": 30},     # Apple Silicon: a first guess, replaced by real timings
    "cpu": {"fast": 70, "split": 440, "transcribe": 40},
}


def detect_device():
    """'cuda' (NVIDIA), 'mps' (Apple Silicon) or 'cpu'. The launcher tells us; otherwise we ask PyTorch."""
    if device_info:
        return device_info
    forced = os.environ.get("KAIROKEE_DEVICE")
    if forced in DEFAULT_PER_MINUTE:
        device_info.update(device=forced, gpu=os.environ.get("KAIROKEE_GPU_NAME") or None)
        return device_info
    dev, name = "cpu", None
    try:
        import torch
        if torch.cuda.is_available():
            dev, name = "cuda", torch.cuda.get_device_name(0)
        elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            dev, name = "mps", "Apple Silicon"
    except Exception:
        pass
    device_info.update(device=dev, gpu=name)
    return device_info


def load_timings():
    try:
        return json.loads(TIMINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def record_timing(kind, seconds, duration):
    """Remember how long a step took per minute of song (fast / split / transcribe), per device.
    The very first run of each step is ignored: it usually includes a one-time model download."""
    try:
        if not duration or duration < 20 or seconds <= 0:
            return
        per_min = seconds / (duration / 60.0)
        dev = detect_device()["device"]
        with timings_lock:
            data = load_timings()
            entry = data.setdefault(dev, {}).setdefault(kind, {"skipped": False, "samples": []})
            if not entry["skipped"]:
                entry["skipped"] = True
            else:
                entry["samples"] = (entry["samples"] + [round(per_min, 1)])[-5:]
            TIMINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass   # timing is only a convenience, never a reason to fail


@app.get("/api/config")
def api_config():
    info = detect_device()
    saved = load_timings().get(info["device"], {})
    per, measured = {}, {}
    for kind, default in DEFAULT_PER_MINUTE[info["device"]].items():
        samples = (saved.get(kind) or {}).get("samples") or []
        per[kind] = round(statistics.median(samples), 1) if samples else default
        measured[kind] = bool(samples)
    return {"device": info["device"], "gpu": info["gpu"], "fast": info["device"] != "cpu",
            "perMinute": per, "measured": measured}


# --------------------------------------------------------------------------- helpers
def song_folder(name):
    """Resolve a song folder safely (no path tricks like ../)."""
    folder = (OUT / name).resolve()
    if folder.parent != OUT.resolve() or not folder.is_dir():
        raise HTTPException(404, "Song not found")
    return folder


def read_meta(folder):
    path = folder / "meta.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def write_meta(folder, meta):
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def song_summary(folder):
    meta = read_meta(folder)
    return {
        "slug": folder.name,
        "title": meta.get("title") or folder.name,
        "hasLyrics": (folder / "lyrics.lrc").exists(),
        "hasVocals": (folder / "vocals.mp3").exists(),
        "hasLead": (folder / "lead.mp3").exists() and (folder / "backing.mp3").exists(),
        "hasCover": (folder / "cover.jpg").exists(),
        "hasPrevious": (folder / "lyrics.previous.lrc").exists(),
    }


# --------------------------------------------------------------------------- API
@app.get("/api/search")
def api_search(q: str):
    q = q.strip()
    if not q:
        return []
    try:
        kk.check_tools()
        return kk.search_youtube(q, 6)
    except kk.KairokeeError as e:
        raise HTTPException(500, str(e))
    except Exception as e:
        raise HTTPException(502, f"Search failed: {e}")


class ProcessRequest(BaseModel):
    id: str = ""
    title: str
    url: str = ""
    channel: str = ""
    duration: float | None = None
    query: str = ""
    split: bool = True      # True = lead and ad-libs separately, False = whole vocals (faster)
    upload: str = ""        # set instead of id/url for an audio file you added yourself (see /api/upload)


UPLOADS = kk.DATA / "uploads"
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,5}$")


@app.put("/api/upload")
async def api_upload(request: Request, name: str = "song"):
    """Receive an audio or video file from the page (the raw file is the request body)."""
    UPLOADS.mkdir(parents=True, exist_ok=True)
    ext = (Path(name).suffix.lower().lstrip(".") or "bin")[:5]
    if not ext.isalnum():
        ext = "bin"
    upload = f"{uuid.uuid4().hex}.{ext}"
    dest = UPLOADS / upload
    size = 0
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            size += len(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "The file is empty.")
    duration = kk.media_duration(dest)
    if not duration:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "This file could not be read. Use an audio or video file (mp3, m4a, wav, flac, mp4...).")
    title = re.sub(r"[_]+", " ", Path(name).stem).strip() or "My song"
    return {"upload": upload, "title": title, "duration": duration}


def clean_old_uploads():
    """Files that were added but never made into a song (page closed...) are removed after a day."""
    if not UPLOADS.exists():
        return
    for f in UPLOADS.iterdir():
        try:
            if time.time() - f.stat().st_mtime > 86400:
                f.unlink()
        except Exception:
            pass


@app.post("/api/process")
def api_process(req: ProcessRequest):
    upload = None
    if req.upload:
        if not UPLOAD_ID.match(req.upload) or not (UPLOADS / req.upload).exists():
            raise HTTPException(400, "The file is gone. Choose it again.")
        upload = UPLOADS / req.upload
        video = {"id": kk.file_id(upload), "title": req.title.strip() or "My song", "url": None,
                 "file": str(upload), "duration": req.duration}
    elif req.id and req.url:
        video = {"id": req.id, "title": req.title, "url": req.url,
                 "channel": req.channel, "duration": req.duration}
    else:
        raise HTTPException(400, "Nothing to make: choose a search result or a file.")
    query = req.query or video["title"]
    name = kk.folder_name(video)
    folder = OUT / name
    is_new = not (folder / "instrumental.mp3").exists()

    def forget_unfinished():
        """Stopped: remove the half-made song folder (only if this was a new song)."""
        if is_new and folder.resolve().parent == OUT.resolve():
            shutil.rmtree(folder, ignore_errors=True)

    def work(job):
        kk.check_tools()
        started = time.time()
        stamps = []   # (stage, time it began)

        def on_stage(s):
            stamps.append((s, time.time()))
            job.update(stage=s, detail="")

        try:
            kk.run_pipeline(video, query, keep="both", split=req.split, folder=folder,
                            stage=on_stage,
                            detail=lambda m: job.__setitem__("detail", m))
        finally:
            if upload:
                upload.unlink(missing_ok=True)   # the song folder has its own copy of the audio now
        finished = time.time()
        spent = {}
        for i, (step, t0) in enumerate(stamps):
            t1 = stamps[i + 1][1] if i + 1 < len(stamps) else finished
            spent[step] = spent.get(step, 0) + (t1 - t0)
        if req.duration:
            record_timing("fast", finished - started - spent.get("split", 0) - spent.get("transcribe", 0),
                          req.duration)
            if "split" in spent:
                record_timing("split", spent["split"], req.duration)
            if "transcribe" in spent:
                record_timing("transcribe", spent["transcribe"], req.duration)

    return {"job": start_job("download", name, work, forget_unfinished)}


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return job


@app.post("/api/job/{job_id}/cancel")
def api_job_cancel(job_id: str):
    """Stop a running job. The helper program doing the heavy work is closed right away."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    if job["state"] == "running":
        kk.CANCEL.set()
        job["detail"] = "Stopping..."
    return {"ok": True}


@app.get("/api/library")
def api_library():
    folders = [f for f in OUT.iterdir() if f.is_dir() and (f / "instrumental.mp3").exists()]
    folders.sort(key=lambda f: (f / "instrumental.mp3").stat().st_mtime, reverse=True)
    return [song_summary(f) for f in folders]


@app.get("/api/song/{slug}")
def api_song(slug: str):
    folder = song_folder(slug)
    meta = read_meta(folder)
    lrc = folder / "lyrics.lrc"
    return {
        **song_summary(folder),
        "lyrics": lrc.read_text(encoding="utf-8") if lrc.exists() else "",
        "offset": meta.get("offset", 0.0),
        "source": meta.get("lyrics_source"),
        "duration": meta.get("video_duration"),
        "key": int(meta.get("key") or 0),
        "recordings": list_recordings(folder),
    }


# --------------------------------------------------------------------------- delete (recoverable)
TRASH = kk.DATA / "deleted"
TRASH_DAYS = 30


@app.delete("/api/song/{slug}")
def api_delete(slug: str):
    """Move a song to the 'deleted' folder. It can be restored for 30 days, then it is removed."""
    folder = song_folder(slug)
    TRASH.mkdir(parents=True, exist_ok=True)
    name = folder.name
    k = 2
    while (TRASH / name).exists():
        name = f"{folder.name} ({k})"
        k += 1
    (folder / ".deleted").write_text(str(time.time()), encoding="utf-8")
    for attempt in range(10):          # the browser may still be letting go of the audio files
        try:
            folder.rename(TRASH / name)
            break
        except PermissionError:
            time.sleep(0.5)
    else:
        (folder / ".deleted").unlink(missing_ok=True)
        raise HTTPException(409, "The song is still in use. Wait a moment and try again.")
    return {"ok": True, "trash": name}


@app.post("/api/trash/{name}/restore")
def api_restore(name: str):
    src = (TRASH / name).resolve()
    if src.parent != TRASH.resolve() or not src.is_dir():
        raise HTTPException(404, "This song is no longer in the deleted songs.")
    dest_name = src.name
    k = 2
    while (OUT / dest_name).exists():
        dest_name = f"{src.name} ({k})"
        k += 1
    src.rename(OUT / dest_name)
    (OUT / dest_name / ".deleted").unlink(missing_ok=True)
    return {"ok": True, "slug": dest_name}


def empty_old_trash():
    if not TRASH.exists():
        return
    for d in TRASH.iterdir():
        try:
            when = float((d / ".deleted").read_text(encoding="utf-8"))
        except Exception:
            when = d.stat().st_mtime
        if time.time() - when > TRASH_DAYS * 86400:
            shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- key change
class KeyBody(BaseModel):
    semitones: int


@app.post("/api/song/{slug}/key")
def api_key(slug: str, body: KeyBody):
    """Make the tracks in another key (a few seconds) and remember the choice for this song."""
    folder = song_folder(slug)
    n = max(-6, min(6, body.semitones))
    with job_lock:
        if any(j["state"] == "running" for j in jobs.values()):
            raise HTTPException(409, "Wait until the song that is being prepared is finished.")
    try:
        kk.render_key(folder, n)
    except kk.KairokeeError as e:
        raise HTTPException(500, str(e))
    meta = read_meta(folder)
    meta["key"] = n
    write_meta(folder, meta)
    return {"ok": True, "key": n, "path": "" if n == 0 else f"key/{n:+d}/"}


# --------------------------------------------------------------------------- recordings
def list_recordings(folder):
    rec = folder / "recordings"
    if not rec.exists():
        return []
    files = sorted(rec.glob("*.mp3"), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size": f.stat().st_size} for f in files]


@app.put("/api/song/{slug}/recording")
async def api_recording(request: Request, slug: str, start: float = 0.0, latency: float = 0.0,
                        music: float = 1.0, key: int = 0, ext: str = "webm"):
    """Receive your singing (recorded in the browser) and save it mixed with the music as an mp3."""
    folder = song_folder(slug)
    rec = folder / "recordings"
    rec.mkdir(exist_ok=True)
    ext = ext if ext.isalnum() and len(ext) <= 5 else "webm"
    raw = rec / f"voice-{uuid.uuid4().hex[:8]}.{ext}"
    with open(raw, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    out = rec / time.strftime("Recording %Y-%m-%d %H-%M-%S.mp3")
    try:
        await asyncio_to_thread(kk.mix_recording, folder, raw, start, latency, music, key, out)
    except kk.KairokeeError as e:
        raise HTTPException(500, str(e))
    finally:
        raw.unlink(missing_ok=True)
    return {"ok": True, "name": out.name}


async def asyncio_to_thread(fn, *args):
    import asyncio
    return await asyncio.to_thread(fn, *args)


@app.delete("/api/song/{slug}/recordings/{name}")
def api_recording_delete(slug: str, name: str):
    folder = song_folder(slug)
    f = (folder / "recordings" / name).resolve()
    if f.parent != (folder / "recordings").resolve() or not f.exists():
        raise HTTPException(404, "Recording not found")
    f.unlink()
    return {"ok": True}


class Settings(BaseModel):
    offset: float


@app.post("/api/song/{slug}/settings")
def api_settings(slug: str, body: Settings):
    folder = song_folder(slug)
    meta = read_meta(folder)
    meta["offset"] = max(-60.0, min(60.0, body.offset))
    write_meta(folder, meta)
    return {"ok": True}


class LyricsBody(BaseModel):
    text: str


@app.post("/api/song/{slug}/lyrics")
def api_lyrics(slug: str, body: LyricsBody):
    folder = song_folder(slug)
    (folder / "lyrics.lrc").write_text(body.text, encoding="utf-8")
    meta = read_meta(folder)
    meta["lyrics_source"] = "edited by you"
    write_meta(folder, meta)
    return {"ok": True}


def video_id_of(meta):
    if meta.get("id"):
        return meta["id"]
    m = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", meta.get("url") or "")
    return m.group(1) if m else None


@app.post("/api/song/{slug}/cover")
def api_cover(slug: str):
    """Fetch cover art for a song that has none (older songs). Tried once per song."""
    folder = song_folder(slug)
    if (folder / "cover.jpg").exists():
        return {"ok": True}
    meta = read_meta(folder)
    if meta.get("cover_tried"):
        return {"ok": False}
    title = meta.get("title") or folder.name
    queries = [q for q in dict.fromkeys(
        [meta.get("query"), kk.clean_title(title), kk.arabic_part(title)]) if q]
    source = kk.fetch_cover(folder, queries, meta.get("video_duration"), video_id_of(meta))
    meta["cover_tried"] = True
    meta["cover"] = source
    write_meta(folder, meta)
    return {"ok": bool(source)}


@app.post("/api/song/{slug}/split")
def api_split(slug: str):
    """Separate lead vocals from backing vocals / ad-libs for a song made before this feature."""
    folder = song_folder(slug)
    vocals = folder / "vocals.mp3"
    if not vocals.exists():
        raise HTTPException(400, "This song has no vocals track to split.")

    def work(job):
        began = time.time()
        kk.split_into_mp3s(vocals, folder, lambda m: job.__setitem__("detail", m))
        meta = read_meta(folder)
        record_timing("split", time.time() - began, meta.get("video_duration"))
        meta["split"] = True
        write_meta(folder, meta)

    return {"job": start_job("split", slug, work, lambda: shutil.rmtree(folder / "split", ignore_errors=True))}


AI_LABEL = "Written by the app"


def stash_previous_lyrics(folder):
    """Keep the current lyrics as 'previous' before replacing them, so nothing good is ever lost.
    Lyrics written by the app are not kept (they never replace better lyrics in the backup)."""
    cur = folder / "lyrics.lrc"
    meta = read_meta(folder)
    if cur.exists() and not str(meta.get("lyrics_source") or "").startswith(AI_LABEL):
        shutil.copyfile(cur, folder / "lyrics.previous.lrc")
        meta["previous_source"] = meta.get("lyrics_source")
        write_meta(folder, meta)


@app.post("/api/song/{slug}/online-lyrics")
def api_online_lyrics(slug: str):
    """Search the online sources again (e.g. to get back good lyrics after trying 'write lyrics')."""
    folder = song_folder(slug)
    meta = read_meta(folder)
    try:
        found = kk.find_online_lyrics(folder, meta)
    except Exception as e:
        raise HTTPException(502, f"The lyrics search failed: {e}")
    if not found:
        return {"ok": False}
    lrc, source = found
    stash_previous_lyrics(folder)
    (folder / "lyrics.lrc").write_text(lrc, encoding="utf-8")
    meta = read_meta(folder)
    meta["lyrics_source"] = source
    write_meta(folder, meta)
    return {"ok": True, "source": source}


candidate_cache = {}   # slug -> {id: LRCLIB entry} from the last search, used by "use-lyrics"


def preview_of(lrc, n=3):
    out = []
    for line in lrc.splitlines():
        text = re.sub(r"\[[^\]]*\]\s*", "", line).strip()
        if text:
            out.append(text)
        if len(out) >= n:
            break
    return out


@app.get("/api/song/{slug}/lyrics-candidates")
def api_lyrics_candidates(slug: str):
    """Online lyrics that could belong to this song, best guess first, so the user can pick."""
    folder = song_folder(slug)
    meta = read_meta(folder)
    queries = kk.build_queries(meta.get("query"), meta.get("title") or folder.name)
    duration = meta.get("video_duration")
    try:
        pool = kk.gather_candidates(queries)
    except Exception as e:
        raise HTTPException(502, f"The lyrics search failed: {e}")
    ranked = kk.rank_candidates(pool, queries, duration)[:8]
    candidate_cache[slug] = {str(c["id"]): c for _, _, _, c, _ in ranked}
    return [{
        "id": str(c["id"]),
        "artist": c.get("artistName") or "",
        "track": c.get("trackName") or "",
        "duration": c.get("duration"),
        "diff": round(abs((c.get("duration") or 0) - duration)) if duration else None,
        "likely": not other_song,
        "preview": preview_of(c["syncedLyrics"]),
    } for other_song, _, _, c, _ in ranked]


class UseLyricsBody(BaseModel):
    id: str


@app.post("/api/song/{slug}/use-lyrics")
def api_use_lyrics(slug: str, body: UseLyricsBody):
    folder = song_folder(slug)
    entry = candidate_cache.get(slug, {}).get(body.id)
    if not entry:
        raise HTTPException(400, "Search again, then pick the lyrics.")
    stash_previous_lyrics(folder)
    (folder / "lyrics.lrc").write_text(entry["syncedLyrics"], encoding="utf-8")
    meta = read_meta(folder)
    meta["lyrics_source"] = f"LRCLIB: {entry.get('artistName', '?')} - {entry.get('trackName', '?')} (chosen by you)"
    write_meta(folder, meta)
    return {"ok": True}


@app.post("/api/song/{slug}/lyrics-swap")
def api_lyrics_swap(slug: str):
    """Swap the current lyrics with the previous ones (pressing again swaps back)."""
    folder = song_folder(slug)
    cur, prev = folder / "lyrics.lrc", folder / "lyrics.previous.lrc"
    if not prev.exists():
        raise HTTPException(400, "There are no previous lyrics for this song.")
    old_cur = cur.read_text(encoding="utf-8") if cur.exists() else ""
    cur.write_text(prev.read_text(encoding="utf-8"), encoding="utf-8")
    if old_cur:
        prev.write_text(old_cur, encoding="utf-8")
    else:
        prev.unlink()
    meta = read_meta(folder)
    meta["lyrics_source"], meta["previous_source"] = meta.get("previous_source"), meta.get("lyrics_source")
    write_meta(folder, meta)
    return {"ok": True}


class TranscribeBody(BaseModel):
    language: str = ""      # "", "ar", "en" or "fr"; empty = guess


@app.post("/api/song/{slug}/transcribe")
def api_transcribe(slug: str, body: TranscribeBody):
    """Write time-synced lyrics from the vocals (replaces the current lyrics)."""
    folder = song_folder(slug)
    if not any((folder / f).exists() for f in ("lead.mp3", "vocals.mp3")):
        raise HTTPException(400, "This song has no vocals track to write lyrics from.")
    meta = read_meta(folder)
    language = body.language if body.language in kk.WHISPER_LANGS else kk.guess_language(
        meta.get("title"), meta.get("query"), folder.name)

    def work(job):
        cur = folder / "lyrics.lrc"
        before = cur.read_text(encoding="utf-8") if cur.exists() else None
        source_before = read_meta(folder).get("lyrics_source")
        began = time.time()
        source = kk.write_lyrics_from_audio(folder, language, lambda m: job.__setitem__("detail", m))
        meta = read_meta(folder)
        record_timing("transcribe", time.time() - began, meta.get("video_duration"))
        # keep the old lyrics so they can be restored (but never keep app-written ones over better ones)
        if before and not str(source_before or "").startswith(AI_LABEL):
            (folder / "lyrics.previous.lrc").write_text(before, encoding="utf-8")
            meta["previous_source"] = source_before
        meta["lyrics_source"] = source
        write_meta(folder, meta)

    return {"job": start_job("transcribe", slug, work)}


# the web page: static/index.html (or index.html next to this file, if you put it there)
INDEX = next((p for p in (ROOT / "static" / "index.html", ROOT / "index.html") if p.exists()), None)


@app.get("/icon.png")
@app.get("/favicon.ico")
def icon():
    return FileResponse(ROOT / "static" / "icon.png", headers={"Cache-Control": "max-age=86400"})


@app.get("/")
def home():
    if INDEX is None:
        raise HTTPException(500, "index.html not found. Put it in a folder named 'static' next to server.py.")
    # no-store: always load the newest page after an update (a stale cached page caused confusion)
    return FileResponse(INDEX, headers={"Cache-Control": "no-store"})


# audio files
app.mount("/files", StaticFiles(directory=OUT), name="files")


# --------------------------------------------------------------------------- run
def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no data is sent; just picks the right network card
            return s.getsockname()[0]
    except Exception:
        return None


if __name__ == "__main__":
    if INDEX is None:
        raise SystemExit("[!] index.html not found. Create a folder named 'static' next to server.py "
                         "and put index.html inside it, then run again.")
    print("\nkairokee is running.")
    print(f"  On this computer:  http://localhost:{PORT}")
    if PHONE_MODE:
        ip = lan_ip()
        if ip:
            print(f"  On your phone:     http://{ip}:{PORT}   (same Wi-Fi)")
        print("  Phone mode is ON: anyone on this Wi-Fi can open the app.")
    else:
        print("  Private mode: only this computer can open the app.")
        phone = "Kairokee-Phone.command" if kk.IS_MAC else "Kairokee-Phone.bat"
        print(f"  (To use it from a phone, open {phone} instead.)")
    print("  To stop Kairokee, close this window.\n")
    threading.Thread(target=detect_device, daemon=True).start()
    for tidy in (empty_old_trash, clean_old_uploads):
        try:
            tidy()
        except Exception as e:
            print(f"[!] {e}")
    uvicorn.run(app, host="0.0.0.0" if PHONE_MODE else "127.0.0.1", port=PORT, log_level="warning")
