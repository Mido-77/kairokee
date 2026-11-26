#!/usr/bin/env python3
"""
kairokee - step 1 (command line)

  search  ->  download  ->  separate vocals / instrumental  ->  fetch synced lyrics

Usage:
    python kairokee.py "tamer hosny ya rab"
    python kairokee.py "shape of you ed sheeran" --keep instrumental
    python kairokee.py "some song" --auto          # pick the first search result

Results are saved in  output/<song name>/
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # so Arabic / French titles print correctly on Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Where songs and models are stored. By default: next to this file (as before).
# The launcher sets KAIROKEE_DATA so that updating the code never touches songs or models.
DATA = Path(os.environ.get("KAIROKEE_DATA") or Path(__file__).parent)
OUT = DATA / "output"
MODEL_DIR = DATA / "models"  # extra models are stored here
if os.environ.get("KAIROKEE_DATA"):
    # keep the separation model (demucs) inside the data folder too, not in the user's profile
    os.environ.setdefault("TORCH_HOME", str(MODEL_DIR / "torch"))
# Lead vs backing-vocals model. gabox_v2 keeps the main singer in the lead file from start to end;
# the older aufr33 model kept flipping the main singing between the two files from section to section.
KARAOKE_MODEL = "mel_band_roformer_karaoke_gabox_v2.ckpt"
OLD_KARAOKE_MODEL = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
# lyrics from audio: "medium" fits a 4 GB GPU. Set KAIROKEE_WHISPER_MODEL=large-v3 for better Arabic (3 GB download)
WHISPER_MODEL = os.environ.get("KAIROKEE_WHISPER_MODEL", "medium")
WHISPER_LANGS = {"ar", "en", "fr"}
MATCH_THRESHOLD = 0.75   # how much of a result's song title must match our search to count as "the same song"
USER_AGENT = "kairokee/0.2 (personal project)"
IS_MAC = sys.platform == "darwin"
# what to open when the installed parts are broken (the message must name the right file on each system)
REPAIR = "Kairokee-Repair.command" if IS_MAC else "Kairokee-Repair.bat"


# --------------------------------------------------------------------------- helpers
class KairokeeError(Exception):
    """Something went wrong in a way we can explain to the user."""


class Cancelled(KairokeeError):
    """The user pressed Stop."""


# Set by the web server when the user presses Stop. Only one job runs at a time, so one flag is enough.
CANCEL = threading.Event()


def die(msg):
    raise KairokeeError(msg)


def check_cancel():
    if CANCEL.is_set():
        raise Cancelled("Stopped.")


def run_child(cmd, env=None):
    """Run a helper program and return its exit code. Stops it right away if the user presses Stop."""
    proc = subprocess.Popen(cmd, env=env)
    while True:
        try:
            return proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            if CANCEL.is_set():
                proc.kill()
                proc.wait()
                raise Cancelled("Stopped.")


def compute_device():
    """'cuda' (NVIDIA), 'mps' (Apple Silicon graphics) or 'cpu'. The launcher decides and tells us;
    without the launcher (command line use) we ask PyTorch."""
    forced = os.environ.get("KAIROKEE_DEVICE")
    if forced in ("cuda", "mps", "cpu"):
        return forced
    try:
        import torch
    except ImportError:
        die(f"PyTorch is not installed. Close Kairokee and open {REPAIR}.")
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def child_env(device):
    """Settings for the helper programs (separation, ad-lib split, lyrics)."""
    env = dict(os.environ)
    env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # Apple: run the few unsupported steps on the CPU
    if device == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = "-1"              # really use the CPU, even if a card is there
    return env


def fmt_duration(seconds):
    if not seconds:
        return "?:??"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def slug(text):
    """Folder name for a song. Windows does not allow a folder name to end in a space or a dot,
    so cut to length FIRST and strip afterwards (a title cut right after a space broke downloads)."""
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80].strip(" .") or "song"


def folder_name(video):
    """Folder name for a song. Making the SAME video (or file) again reuses its folder; a different
    song with the same title gets "Title (2)", "Title (3)"... so nothing is ever overwritten."""
    base = slug(video["title"])
    vid = video.get("id")
    for k in range(1, 100):
        name = base if k == 1 else f"{base[:74].strip(' .')} ({k})"
        folder = OUT / name
        if not folder.exists():
            return name
        try:
            other = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("id")
        except Exception:
            other = None
        if vid and other == vid:
            return name
        if not vid and not other and not (folder / "instrumental.mp3").exists():
            return name          # an unfinished folder without any song in it
    die("Too many songs with this title.")


def file_id(path):
    """A stable id for an audio file you added yourself (same file = same id = same song folder)."""
    import hashlib

    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "file:" + h.hexdigest()[:16]


def media_duration(path):
    """Length in seconds (ffprobe), or None."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=60)
        return round(float(r.stdout.strip()), 1)
    except Exception:
        return None


def import_audio(src, folder):
    """Your own audio (or video) file -> folder/original.wav."""
    print(f"\n[1/3] Reading your file {Path(src).name} ...")
    wav = folder / "original.wav"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "2", str(wav)])
    if r.returncode != 0 or not wav.exists():
        die("This file could not be read. Use an audio or video file (mp3, m4a, wav, flac, mp4...).")
    return wav


def clean_title(title):
    """'Artist - Song (Official Video)' -> 'Artist - Song' (better for lyrics search)."""
    title = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", title)
    title = re.sub(
        r"(?i)\b(official|music video|lyric video|lyrics?|video|audio|clip|hd|4k)\b", " ", title
    )
    return re.sub(r"\s+", " ", title).strip(" -|")


def check_tools():
    if not shutil.which("ffmpeg"):
        die(f"ffmpeg not found. Close Kairokee and open {REPAIR}.")
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        die(f"yt-dlp is not installed. Close Kairokee and open {REPAIR}.")


# --------------------------------------------------------------------------- 1. search
def search_youtube(query, n):
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    results = []
    for e in info.get("entries", []):
        if not e:
            continue
        results.append(
            {
                "id": e["id"],
                "title": e.get("title", "?"),
                "channel": e.get("channel") or e.get("uploader") or "?",
                "duration": e.get("duration"),
                "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
            }
        )
    return results


def pick_result(results, auto):
    print("\nSearch results:")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['title']}  [{fmt_duration(r['duration'])}]  - {r['channel']}")
    if auto:
        return results[0]
    while True:
        choice = input("\nWhich one? (number, or Enter for 1): ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice) - 1]
        print("Please type a number from the list.")


# --------------------------------------------------------------------------- 2. download
class _YtdlpLog:
    """Collects what yt-dlp says, prints it in the black window, and lets us show the real reason."""

    def __init__(self):
        self.lines = []

    def _add(self, msg):
        msg = str(msg)
        self.lines.append(msg)
        print("      yt-dlp: " + msg)

    def debug(self, msg):
        if not str(msg).startswith("[debug]"):
            self._add(msg)

    def info(self, msg):
        self._add(msg)

    def warning(self, msg):
        self._add("WARNING: " + str(msg))

    def error(self, msg):
        self._add("ERROR: " + str(msg))

    def last_problem(self):
        for line in reversed(self.lines):
            if line.startswith(("ERROR", "WARNING")):
                return line
        return ""


def download_audio(video, folder):
    import yt_dlp

    log = _YtdlpLog()

    def stop_if_cancelled(_progress):
        check_cancel()

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(folder / "original.%(ext)s"),
        "noplaylist": True,
        "noprogress": True,
        "logger": log,                        # everything yt-dlp says goes to the Kairokee window
        "progress_hooks": [stop_if_cancelled],
        "js_runtimes": {"deno": {}},          # YouTube needs a JavaScript runtime (Deno) to give out audio
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    }
    print("\n[1/3] Downloading audio...")
    code = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            code = ydl.download([video["url"]])
    except Exception as e:
        check_cancel()   # pressing Stop also ends up here: report it as a stop, not as a failure
        (folder / "download.log").write_text("\n".join(log.lines), encoding="utf-8")
        die(f"Download failed: {log.last_problem() or type(e).__name__ + ': ' + str(e)}")

    wav = folder / "original.wav"
    if not wav.exists():
        # the audio may have been downloaded but not converted: do the conversion ourselves
        left = [p for p in folder.glob("original.*") if p.suffix.lower() not in (".wav", ".part", ".ytdl")]
        if left:
            print(f"      converting {left[0].name} to wav...")
            try:
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(left[0]), "-vn", str(wav)],
                               check=True)
            except Exception as e:
                print(f"      conversion failed: {e}")
    if not wav.exists():
        names = ", ".join(p.name for p in folder.iterdir()) or "nothing"
        (folder / "download.log").write_text("\n".join(log.lines), encoding="utf-8")
        die("Download failed: " + (log.last_problem() or
            f"yt-dlp finished (code {code}) but made no audio file. The song folder holds: {names}."))
    return wav


# --------------------------------------------------------------------------- 3. separate
DEVICE_NAMES = {"cuda": "NVIDIA graphics card", "mps": "Apple graphics (Metal)", "cpu": "processor (CPU)"}
CPU_FALLBACK_MSG = ("The graphics chip could not be used for this step, so it is using the processor. "
                    "This is slower. The app is working, it is not frozen.")


def demucs_worker(args):
    """Runs in its own process (a crash here cannot take the server down).
    Splits a song into vocals and everything else with Demucs.  args: wav out_dir device segment
    The audio is read with ffmpeg and written with soundfile, so this works with any PyTorch version
    (newer torchaudio versions can no longer save files on their own)."""
    wav_path, out_dir, device, segment = (args + ["", "", "", ""])[:4]
    # Demucs model files are older-style pickles that PyTorch 2.6+ refuses to load by default
    os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import torch
    import soundfile as sf
    from demucs.apply import apply_model
    from demucs.audio import AudioFile
    from demucs.pretrained import get_model

    model = get_model("htdemucs")
    model.cpu()
    model.eval()
    wav = AudioFile(Path(wav_path)).read(streams=0, samplerate=model.samplerate, channels=model.audio_channels)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std()
    if float(std) < 1e-8:
        std = torch.tensor(1.0)
    wav = (wav - mean) / std
    with torch.no_grad():
        sources = apply_model(model, wav[None], device=device, shifts=1, split=True, overlap=0.25,
                              progress=True, segment=float(segment) if segment else None)[0]
    sources = sources * std + mean
    vi = model.sources.index("vocals")
    vocals = sources[vi]
    rest = sources.sum(0) - vocals
    for name, x in (("vocals", vocals), ("no_vocals", rest)):
        x = x / max(1.01 * float(x.abs().max()), 1.0)    # like Demucs' default: scale down instead of clipping
        sf.write(str(Path(out_dir) / f"{name}.wav"), x.cpu().numpy().T, model.samplerate, subtype="PCM_16")


def separate_vocals(wav, folder, segment=None, say=lambda msg: None):
    if not segment and os.environ.get("KAIROKEE_SEGMENT"):   # set by the launcher for GPUs with little memory
        segment = int(os.environ["KAIROKEE_SEGMENT"])
    device = compute_device()
    print(f"\n[2/3] Separating vocals from instrumental on the {DEVICE_NAMES[device]} "
          "(first run downloads the model, ~80 MB)...")
    work = folder / "demucs"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    vocals, instrumental = work / "vocals.wav", work / "no_vocals.wav"
    for dev in ([device, "cpu"] if device != "cpu" else ["cpu"]):
        if dev != device:
            print("[!] Separation on the graphics chip failed - trying again on the processor (slower).")
            say(CPU_FALLBACK_MSG)
        cmd = [sys.executable, str(Path(__file__).resolve()), "--demucs-worker",
               str(wav), str(work), dev, str(segment or "")]
        if run_child(cmd, child_env(dev)) == 0 and vocals.exists() and instrumental.exists():
            return vocals, instrumental
    die("Separation failed - read the error printed in the Kairokee window. If it says 'out of memory', "
        "close other programs and try again.")


def to_mp3(src, dst):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-b:a", "320k", str(dst)],
        check=True,
    )


# --------------------------------------------------------------------------- key change and recordings
STEM_FILES = ("instrumental.mp3", "vocals.mp3", "lead.mp3", "backing.mp3")
_filters = {}


def has_ffmpeg_filter(name):
    if name not in _filters:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30).stdout
            _filters[name] = any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())
        except Exception:
            _filters[name] = False
    return _filters[name]


def pitch_filter(semitones, voice=False):
    """ffmpeg filter that moves the key by some semitones and keeps the speed.
    rubberband sounds best (and keeps voices natural); not every ffmpeg has it, so there is a fallback."""
    r = 2 ** (semitones / 12)
    if has_ffmpeg_filter("rubberband"):
        return f"rubberband=pitch={r:.6f}:pitchq=quality" + (":formant=preserved" if voice else "")
    return f"aresample=44100,asetrate={44100 * r:.3f},aresample=44100,atempo={1 / r:.6f}"


def key_folder(folder, semitones):
    return folder / "key" / f"{semitones:+d}"


def render_key(folder, semitones):
    """Make the song's tracks in another key (kept in folder/key/+2/...). Only one other key is kept,
    to save disk space. Returns the folder that holds the tracks."""
    if semitones == 0:
        return folder
    dest = key_folder(folder, semitones)
    for old in (folder / "key").glob("*") if (folder / "key").exists() else []:
        if old != dest:
            shutil.rmtree(old, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    # only the tracks the player uses: the singer + ad-libs, or the whole vocals when they are not split
    split = (folder / "lead.mp3").exists() and (folder / "backing.mp3").exists()
    names = ["instrumental.mp3"] + (["lead.mp3", "backing.mp3"] if split else ["vocals.mp3"])
    todo = []
    for name in names:
        src, out = folder / name, dest / name
        if src.exists() and not (out.exists() and out.stat().st_mtime >= src.stat().st_mtime):
            todo.append(name)

    def one(name):
        tmp = dest / ("tmp-" + name)
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(folder / name),
                            "-af", pitch_filter(semitones, voice=name != "instrumental.mp3"),
                            "-b:a", "320k", str(tmp)])
        if r.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest / name)
        return True

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, len(todo))) as pool:   # all tracks at the same time
        results = list(pool.map(one, todo))
    if not all(results):
        die("Could not change the key of this song (ffmpeg failed - see the Kairokee window).")
    return dest


def mix_recording(folder, voice_file, start, latency, music_volume, semitones, out_file):
    """Your singing + the music, from the moment you pressed Record, as one mp3.
    start: song position (s) when recording began. latency: how much later the voice arrived (s)."""
    music = key_folder(folder, semitones) / "instrumental.mp3" if semitones else folder / "instrumental.mp3"
    if not music.exists():
        music = folder / "instrumental.mp3"
    vol = max(0.0, min(1.5, music_volume))
    # the voice is moved earlier by the delay of speakers + microphone, then both are mixed
    voice_chain = f"[1:a]atrim=start={max(0.0, latency):.3f},asetpts=PTS-STARTPTS,aresample=44100,volume=1.6[v]"
    music_chain = f"[0:a]atrim=start={max(0.0, start):.3f},asetpts=PTS-STARTPTS,aresample=44100,volume={vol:.2f}[m]"
    graph = f"{voice_chain};{music_chain};[m][v]amix=inputs=2:duration=shortest:normalize=0,alimiter=limit=0.95[out]"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(music), "-i", str(voice_file),
                        "-filter_complex", graph, "-map", "[out]", "-b:a", "256k", str(out_file)])
    if r.returncode != 0 or not Path(out_file).exists():
        die("Could not save the recording (ffmpeg failed - see the Kairokee window).")


# --------------------------------------------------------------------------- 4. lyrics
ARABIC = re.compile(r"[\u0600-\u06FF]")


def arabic_part(title):
    """Only the Arabic words of a video title (good extra query for Arabic songs)."""
    return " ".join(re.findall(r"[\u0600-\u06FF]+", title))


def lrclib_candidates(query):
    """Ask LRCLIB (free, no key). Returns the entries that have time-synced lyrics."""
    url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        results = json.load(resp)
    return [r for r in results if r.get("syncedLyrics")]


AR_MARKS = re.compile(r"[\u064B-\u065F\u0670\u0640]")   # Arabic diacritics and the tatweel stretch
AR_FOLD = str.maketrans({"\u0623": "\u0627", "\u0625": "\u0627", "\u0622": "\u0627",
                         "\u0649": "\u064A", "\u0629": "\u0647"})


def _tokens(text):
    """Lower-case words, with Arabic spelling variants folded together (diacritics, alef forms...)."""
    text = AR_MARKS.sub("", text.lower().replace("'", "").replace("\u2019", "")).translate(AR_FOLD)
    return re.findall(r"\w+", text)


def relevance(candidate, query_tokens):
    """Share of the entry's SONG TITLE words that also appear in our search words (0..1).
    The artist's own name is ignored, otherwise another song by the same artist would match."""
    words = _tokens(clean_title(candidate.get("trackName") or ""))
    artist = set(_tokens(candidate.get("artistName") or ""))
    core = [w for w in words if w not in artist] or words
    if not core:
        return 0.0
    hits = sum(
        1 for w in core
        if any(w == q or difflib.SequenceMatcher(None, w, q).ratio() >= 0.8 for q in query_tokens)
    )
    return hits / len(core)


def build_queries(query, title):
    """Search words: what you typed, the whole video title, its Arabic part, and each title segment
    on its own (so 'SHOFT KALAM' is searched without the artists' names drowning it out)."""
    segments = [clean_title(x) for x in re.split(r"\s[-\u2013\u2014]\s|\s?\|\s?", title or "")]
    queries = []
    for q in [query, clean_title(title or ""), arabic_part(title or "")] + segments:
        q = AR_MARKS.sub("", q or "").strip()
        if len(q) >= 3 and q not in queries:
            queries.append(q)
    return queries[:8]


def gather_candidates(queries):
    """All LRCLIB entries with synced lyrics for our search words (duplicates removed)."""
    pool, seen = [], set()
    for q in queries:
        try:
            for c in lrclib_candidates(q):
                if c.get("id") not in seen:
                    seen.add(c.get("id"))
                    pool.append(c)
        except Exception as e:  # network hiccup, API change...
            print(f"      LRCLIB error for '{q}': {e}")
    return pool


def rank_candidates(pool, queries, duration):
    """Best first. Returns [(other_song, latin_only, length_diff, entry, relevance)]."""
    query_tokens = set(t for q in queries for t in _tokens(q))
    wants_arabic = any(ARABIC.search(q) for q in queries)
    ranked = []
    for c in pool:
        rel = relevance(c, query_tokens)
        latin_only = wants_arabic and not ARABIC.search(c["syncedLyrics"])
        diff = abs((c.get("duration") or 0) - duration) if duration else 0
        ranked.append((rel < MATCH_THRESHOLD, latin_only, diff, c, rel))
    ranked.sort(key=lambda x: x[:3])
    return ranked


def choose_best(pool, queries, duration):
    """Keep only entries that really are this song, then prefer Arabic script, then closest length."""
    ranked = rank_candidates(pool, queries, duration)
    if not ranked or ranked[0][0]:        # nothing matches the song title
        return None
    _, latin_only, diff, best, _ = ranked[0]
    label = f"LRCLIB: {best.get('artistName', '?')} - {best.get('trackName', '?')}"
    if duration and diff > 8:
        label += f"  (length differs by {int(diff)}s - sync may be off)"
    if latin_only:
        label += "  (Latin letters only - no Arabic-script version found)"
    return best["syncedLyrics"], label


def fetch_lyrics(queries, duration):
    print("\n[3/3] Looking for synced lyrics online...")
    pool = gather_candidates(queries)
    if pool:
        print(f"      {len(pool)} candidate(s) with synced lyrics")
        best = choose_best(pool, queries, duration)
        if best:
            return best
        print("      none of them matches the song title - ignoring them")
    # Other providers (syncedlyrics) give no song info we could check, so they often return the wrong
    # song. They are only used if you ask for it: set KAIROKEE_EXTRA_LYRICS=1
    if os.environ.get("KAIROKEE_EXTRA_LYRICS") == "1":
        try:
            import syncedlyrics

            for q in queries:
                lrc = syncedlyrics.search(q, synced_only=True)
                if lrc:
                    return lrc, "syncedlyrics (other providers, not verified)"
        except Exception as e:
            print(f"      syncedlyrics error: {e}")
    return None


def find_online_lyrics(folder, meta):
    """Search the online sources again for a song that already exists. Returns (lrc, source) or None."""
    title = meta.get("title") or folder.name
    return fetch_lyrics(build_queries(meta.get("query"), title), meta.get("video_duration"))


# --------------------------------------------------------------------------- lyrics from audio (Whisper)
def guess_language(*texts):
    """'ar' if any text has Arabic letters, else None (Whisper then detects the language itself)."""
    return "ar" if any(t and ARABIC.search(t) for t in texts) else None


def _add_torch_dlls():
    """On Windows, reuse the CUDA libraries that PyTorch already ships (no CUDA install needed)."""
    if os.name != "nt":
        return
    try:
        import importlib.util

        spec = importlib.util.find_spec("torch")
        if spec and spec.submodule_search_locations:
            lib = Path(list(spec.submodule_search_locations)[0]) / "lib"
            if lib.exists():
                os.add_dll_directory(str(lib))
                os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def whisper_worker(args):
    """Runs in its own process (a crash here cannot take the server down).
    args: audio_path out_json language device"""
    audio, out_json, language, device = args[:4]
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # harmless Windows warning
    _add_torch_dlls()
    from faster_whisper import WhisperModel

    compute = "int8_float16" if device == "cuda" else "int8"
    model = WhisperModel(
        WHISPER_MODEL, device=device, compute_type=compute,
        cpu_threads=max(4, (os.cpu_count() or 8) // 2),
        download_root=str(MODEL_DIR / "whisper"),
    )
    segments, info = model.transcribe(
        audio, language=language or None, beam_size=5, word_timestamps=True,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        if seg.no_speech_prob > 0.85 and seg.avg_logprob < -1.0:
            continue  # almost certainly a hallucination over silence
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"w": text, "s": round(w.start, 2), "e": round(w.end, 2)})
    Path(out_json).write_text(
        json.dumps({"language": info.language, "words": words}, ensure_ascii=False), encoding="utf-8")


def words_to_lines(words, max_words=9, max_gap=0.8):
    """Group timed words into lyric lines: break at pauses, long lines and sentence ends."""
    lines, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > max_gap or len(cur) >= max_words):
            lines.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?\u061F\u2026]$", w["w"]) and len(cur) >= 3:
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return [(ln[0]["s"], " ".join(x["w"] for x in ln), ln) for ln in lines]


def _stamp(t):
    m, sec = int(t // 60), t - 60 * int(t // 60)
    return f"{m:02d}:{sec:05.2f}"


def lines_to_lrc(lines):
    """Lines -> LRC text. When the words' own times are known, they are written too
    ("enhanced LRC": [00:08.56] <00:08.56>In <00:08.90>your ...), so the player can light up
    each word exactly when it is sung."""
    out = []
    for line in lines:
        t, text = line[0], line[1]
        words = line[2] if len(line) > 2 else None
        if words:
            text = " ".join(f"<{_stamp(w['s'])}>{w['w']}" for w in words)
        out.append(f"[{_stamp(t)}] {text}")
    return "\n".join(out) + "\n"


def transcribe_to_lrc(audio, language=None, say=lambda msg: None):
    """Listen to a (vocals) audio file and write time-synced lyrics. Returns (lrc_text, language)."""
    import importlib.util

    if importlib.util.find_spec("faster_whisper") is None:
        die(f"Writing lyrics from audio is not installed. Close Kairokee and open {REPAIR}.")
    audio = Path(audio)
    cache = MODEL_DIR / "whisper"
    first_time = not (cache.exists() and any(cache.glob(f"*faster-whisper-{WHISPER_MODEL}*")))
    out = audio.parent / "whisper_words.json"
    out.unlink(missing_ok=True)

    import time

    started = time.time()
    used = None
    # faster-whisper can use NVIDIA cards but not Apple graphics (on a Mac it runs on the fast Apple CPU)
    devices = ["cuda", "cpu"] if compute_device() == "cuda" else ["cpu"]
    for device in devices:
        if device == devices[0]:
            size = {"small": "0.5 GB", "medium": "1.5 GB", "large-v3": "3 GB"}.get(WHISPER_MODEL, "over 1 GB")
            say(f"First time only: downloading the lyrics model (about {size}). This can take 10 minutes or more."
                if first_time else "")   # otherwise the page shows the expected time for this computer
        else:
            say(CPU_FALLBACK_MSG)
        cmd = [sys.executable, str(Path(__file__).resolve()), "--whisper-worker",
               str(audio), str(out), language or "", device]
        if run_child(cmd, child_env(device)) == 0 and out.exists():
            used = device
            break
    else:
        die("Could not write the lyrics from the audio. Read the error printed in the Kairokee window.")

    data = json.loads(out.read_text(encoding="utf-8"))
    out.unlink(missing_ok=True)
    lines = words_to_lines(data["words"])
    print(f"      lyrics written on the {'GPU' if used == 'cuda' else 'CPU'} in "
          f"{int(time.time() - started)} s: {len(lines)} lines, language: {data.get('language')}")
    if not lines:
        die("No singing was detected in the audio, so no lyrics could be written.")
    return lines_to_lrc(lines), data.get("language")


def write_lyrics_from_audio(folder, language=None, say=lambda msg: None, fallback_audio=None):
    """Transcribe lead.mp3 (or vocals.mp3) into folder/lyrics.lrc. Returns the source label."""
    src = next((f for f in (folder / "lead.mp3", folder / "vocals.mp3") if f.exists()), fallback_audio)
    if not src:
        die("This song has no vocals track to write lyrics from.")
    lrc, lang = transcribe_to_lrc(src, language, say)
    (folder / "lyrics.lrc").write_text(lrc, encoding="utf-8")
    return f"Written by the app from the audio (Whisper {WHISPER_MODEL}, language: {lang})"


# --------------------------------------------------------------------------- lead vs backing vocals
def _rms(path):
    import numpy as np
    import soundfile as sf

    data, _ = sf.read(str(path), dtype="float32")
    return float(np.sqrt((data ** 2).mean())) if data.size else 0.0


def split_worker(args):
    """Runs in its own process (a crash or a full graphics memory here cannot take the server down).
    args: vocals_path work_dir device"""
    vocals_path, work_dir, device = (args + ["", "", ""])[:3]
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import logging

    import torch
    if device == "cpu":   # the splitter picks the graphics chip by itself: hide it to really use the CPU
        torch.cuda.is_available = lambda: False
        if getattr(torch.backends, "mps", None) is not None:
            torch.backends.mps.is_available = lambda: False
    from audio_separator.separator import Separator

    model = os.environ.get("KAIROKEE_KARAOKE_MODEL") or KARAOKE_MODEL
    precision = (os.environ.get("KAIROKEE_SPLIT_PRECISION") or "fp32").lower()   # fp32 | autocast | fp16
    extra = {}
    if device != "cpu" and precision == "autocast":
        extra["use_autocast"] = True
    elif device != "cpu" and precision == "fp16":
        extra["use_native_fp16"] = True
    sep = Separator(output_dir=str(work_dir), output_format="WAV",
                    model_file_dir=str(MODEL_DIR), log_level=logging.WARNING, **extra)
    sep.load_model(model_filename=model)
    print(f"      ad-lib splitter: model {model}, device {getattr(sep, 'torch_device', '?')}, precision {precision}")
    print("      splitting lead vocals from backing vocals...")
    sep.separate(str(vocals_path))


def split_vocals(vocals_path, folder, say=lambda msg: None):
    """Split a vocals track into lead vocals and backing vocals / ad-libs. Returns (lead_wav, backing_wav)."""
    import importlib.util

    if importlib.util.find_spec("audio_separator") is None:
        die(f"The ad-lib splitter is not installed. Close Kairokee and open {REPAIR}.")
    work = folder / "split"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model = os.environ.get("KAIROKEE_KARAOKE_MODEL") or KARAOKE_MODEL
    device = compute_device()
    files = []
    for dev in ([device, "cpu"] if device != "cpu" else ["cpu"]):
        if dev != device:
            print("[!] The ad-lib split on the graphics chip failed - trying again on the processor (slower).")
            say(CPU_FALLBACK_MSG)
        elif not (MODEL_DIR / model).exists():
            say("Downloading the ad-lib model (first time only, about 900 MB). This can take 10 minutes or more.")
            print("      downloading the ad-lib model (first time only, ~900 MB)...")
        else:
            say("")   # the page shows the expected time for this computer
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        cmd = [sys.executable, str(Path(__file__).resolve()), "--split-worker", str(vocals_path), str(work), dev]
        if run_child(cmd, child_env(dev)) == 0:
            files = list(work.glob("*.wav"))
            if len(files) >= 2:
                break
    if not files:
        die("The ad-lib split failed - read the error printed in the Kairokee window.")
    lead = next((f for f in files if any(k in f.name.lower() for k in ("(vocals)", "(karaoke)", "(lead)"))), None)
    back = next((f for f in files if f != lead and any(
        k in f.name.lower() for k in ("(instrumental)", "(other)", "(backing)"))), None)
    if lead and not back:
        back = next((f for f in files if f != lead), None)    # two files: the one that is not the lead
    if not (lead and back):
        die("Unexpected files from the ad-lib splitter: " + ", ".join(f.name for f in files))
    # safety net: the lead voice is normally the louder of the two
    if _rms(lead) < 0.5 * _rms(back):
        lead, back = back, lead
    return lead, back


def split_into_mp3s(vocals_path, folder, say=lambda msg: None):
    """Run split_vocals and save lead.mp3 / backing.mp3 next to the other tracks."""
    lead, back = split_vocals(vocals_path, folder, say)
    to_mp3(lead, folder / "lead.mp3")
    to_mp3(back, folder / "backing.mp3")
    shutil.rmtree(folder / "split", ignore_errors=True)


# --------------------------------------------------------------------------- cover art
def _download(url, dest, min_bytes=2000):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
    if len(data) < min_bytes:
        return False
    dest.write_bytes(data)
    return True


def itunes_cover_url(queries, duration):
    """Album art from the free iTunes Search API (no key). Only accepts entries matching the title."""
    query_tokens = set(t for q in queries for t in _tokens(q))
    pool = []
    for q in queries:
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
            {"term": q, "media": "music", "entity": "song", "limit": 10})
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            pool += json.load(resp).get("results", [])
    pool = [c for c in pool if c.get("artworkUrl100") and relevance(c, query_tokens) >= MATCH_THRESHOLD]
    if not pool:
        return None

    def artist_known(c):   # the artist's name is in our search words (else it may be another song with this title)
        return any(t in query_tokens for t in _tokens(c.get("artistName") or "") if len(t) > 2)

    def length_diff(c):
        return abs((c.get("trackTimeMillis") or 0) / 1000 - duration) if duration else 0

    pool.sort(key=lambda c: (not artist_known(c), length_diff(c)))
    best = pool[0]
    if not artist_known(best) and (not duration or length_diff(best) > 10):
        return None        # same title, unknown artist, different length: probably another song
    return best["artworkUrl100"].replace("100x100bb", "600x600bb")


def fetch_cover(folder, queries, duration=None, video_id=None):
    """Save folder/cover.jpg. Returns "itunes", "youtube" or None."""
    dest = folder / "cover.jpg"
    try:
        url = itunes_cover_url(queries, duration)
        if url and _download(url, dest):
            return "itunes"
    except Exception as e:
        print(f"      cover (iTunes) error: {e}")
    if video_id:
        for name in ("maxresdefault", "mqdefault"):
            try:
                if _download(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", dest, 3000):
                    return "youtube"
            except Exception:
                continue
    return None


# --------------------------------------------------------------------------- pipeline
def run_pipeline(video, query, keep="both", keep_temp=False, segment=None,
                 stage=lambda name: None, choose_keep=None, split=True,
                 detail=lambda msg: None, transcribe=True, folder=None):
    """download -> separate -> encode -> lyrics. Used by both the CLI and the web server."""
    folder = folder or OUT / folder_name(video)
    folder.mkdir(parents=True, exist_ok=True)

    stage("download")
    wav = import_audio(video["file"], folder) if video.get("file") else download_audio(video, folder)

    check_cancel()
    stage("separate")
    vocals, instrumental = separate_vocals(wav, folder, segment, detail)
    check_cancel()

    if keep is None and choose_keep:
        keep = choose_keep()
    keep = keep or "both"

    stage("encode")
    if keep in ("instrumental", "both"):
        to_mp3(instrumental, folder / "instrumental.mp3")
    if keep in ("vocals", "both"):
        to_mp3(vocals, folder / "vocals.mp3")

    split_ok = False
    if split and keep in ("vocals", "both"):
        stage("split")
        try:
            split_into_mp3s(vocals, folder, detail)
            split_ok = True
        except Cancelled:
            raise
        except Exception as e:  # not fatal: the song still works without the ad-lib split
            print(f"\n[!] Could not split the ad-libs: {e}\n")

    check_cancel()
    stage("lyrics")
    queries = build_queries(query, video["title"])
    found = fetch_lyrics(queries, video["duration"])
    source = None
    if found:
        lrc, source = found
        (folder / "lyrics.lrc").write_text(lrc, encoding="utf-8")
        print(f"      found -> {source}")
    else:
        print("      no synced lyrics found online.")

    yt_id = video.get("id") if not str(video.get("id") or "").startswith("file:") else None
    cover = fetch_cover(folder, queries, video.get("duration"), yt_id)
    print(f"      cover art: {cover or 'not found'}")

    # no synced lyrics online: write them from the singing itself
    if not found and transcribe:
        stage("transcribe")
        try:
            source = write_lyrics_from_audio(folder, guess_language(query, video["title"]),
                                             detail, fallback_audio=vocals)
            print(f"      lyrics: {source}")
        except Cancelled:
            raise
        except Exception as e:  # not fatal: the song works without lyrics
            print(f"\n[!] Could not write lyrics from the audio: {e}\n")

    # keep the timing offset the user already saved for this song, if any
    meta_path = folder / "meta.json"
    old = {}
    if meta_path.exists():
        try:
            old = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    meta = {
        "title": video["title"],
        "query": query,
        "id": video.get("id"),
        "url": video.get("url"),
        "video_duration": video.get("duration"),
        "lyrics_source": source,
        "cover": cover,
        "split": split_ok,
        "offset": old.get("offset", 0.0),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if not keep_temp:
        wav.unlink(missing_ok=True)
        shutil.rmtree(folder / "demucs", ignore_errors=True)
    return folder, meta


# --------------------------------------------------------------------------- command line
def main():
    workers = {"--whisper-worker": whisper_worker, "--demucs-worker": demucs_worker,
               "--split-worker": split_worker}
    if len(sys.argv) > 1 and sys.argv[1] in workers:
        workers[sys.argv[1]](sys.argv[2:])
        return
    ap = argparse.ArgumentParser(description="kairokee step 1: search, download, separate, lyrics")
    ap.add_argument("query", nargs="+", help="song name (and artist)")
    ap.add_argument("--results", type=int, default=5, help="how many search results to show")
    ap.add_argument("--auto", action="store_true", help="use the first search result")
    ap.add_argument("--keep", choices=["instrumental", "vocals", "both"],
                    help="what to keep (asked interactively if omitted)")
    ap.add_argument("--segment", type=int, help="demucs chunk size in seconds (try 4 if the GPU runs out of memory)")
    ap.add_argument("--keep-temp", action="store_true", help="keep original.wav and demucs/ folder")
    args = ap.parse_args()

    def ask_keep():
        print("\nWhat do you want to keep?")
        print("  1. Instrumental only (karaoke)")
        print("  2. Vocals only (acapella)")
        print("  3. Both")
        return {"1": "instrumental", "2": "vocals", "3": "both"}.get(
            input("Choice (1/2/3, Enter for 1): ").strip() or "1", "instrumental"
        )

    query = " ".join(args.query)
    try:
        check_tools()
        print(f"Searching for: {query}")
        results = search_youtube(query, args.results)
        if not results:
            die("No results found.")
        video = pick_result(results, args.auto)
        folder, _ = run_pipeline(video, query, keep=args.keep, keep_temp=args.keep_temp,
                                 segment=args.segment, choose_keep=ask_keep)
    except KairokeeError as e:
        print(f"\n[!] {e}")
        sys.exit(1)

    print(f"\nDone! Files are in:\n  {folder}")
    for f in sorted(folder.iterdir()):
        print(f"   - {f.name}")


if __name__ == "__main__":
    main()
