"""
Kairokee benchmark - how fast (and how well) does the singer / ad-lib splitter run on THIS PC?

It cuts a short clip from a song you already made, runs the splitter on it in different ways,
and prints how long each way takes. The result files are saved so you can LISTEN and compare.

Usage (PowerShell, from the Kairokee folder):
    $kh = "$env:LOCALAPPDATA\\Kairokee"
    & "$kh\\venv\\Scripts\\python.exe" tools\\bench_split.py "$kh\\data\\output\\<song folder>"

Usage (Mac Terminal, from the Kairokee folder):
    kh="$HOME/Library/Application Support/Kairokee"
    "$kh/venv/bin/python" tools/bench_split.py "$kh/data/output/<song folder>"

Options:
    --seconds 30                 length of the test clip
    --start 40                   where the clip starts (seconds)
    --precision fp32,autocast    ways to run (fp32 = normal, autocast / fp16 = faster on many GPUs)
    --models a.ckpt,b.ckpt       compare different splitter models (names as in audio-separator)
                                 or  --models all  for a short built-in list
    --input vocals|mix           what the splitter listens to: the singer-only track (what Kairokee
                                 does now) or the full song (music + voices)

The table also shows how loud the "lead" file is compared with the "backing" file, second by second.
For a song where one main singer carries the verses, the lead file should be the louder one most of
the time. A lead file that is almost silent means the model gave the main singing to the backing file.
"""
import argparse
import gc
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))

if not os.environ.get("KAIROKEE_DATA"):
    if sys.platform == "darwin":
        default_home = os.path.expanduser("~/Library/Application Support/Kairokee")
    else:
        default_home = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Kairokee")
    base = os.environ.get("KAIROKEE_HOME") or default_home
    os.environ["KAIROKEE_DATA"] = os.path.join(base, "data")
for extra in ("ffmpeg", "deno"):
    p = Path(os.environ["KAIROKEE_DATA"]).parent / extra
    if p.exists():
        os.environ["PATH"] = str(p) + os.pathsep + os.environ["PATH"]

import kairokee as kk  # noqa: E402

MODEL_LIST = [
    kk.KARAOKE_MODEL,                      # the default Kairokee uses now
    kk.OLD_KARAOKE_MODEL,                  # the previous default
    "mel_band_roformer_karaoke_gabox.ckpt",
    "bs_roformer_karaoke_frazer_becruily.ckpt",
]


def make_clip(song, seconds, start, dest, source):
    voc = song / "vocals.mp3"
    inst = song / "instrumental.mp3"
    if not voc.exists():
        sys.exit("[!] This song folder has no vocals.mp3. Pick a song that was made with Kairokee.")
    if not shutil.which("ffmpeg"):
        sys.exit("[!] ffmpeg was not found.")
    base = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(seconds)]
    if source == "mix":
        if not inst.exists():
            sys.exit("[!] This song folder has no instrumental.mp3, so the full song cannot be rebuilt.")
        cmd = base + ["-i", str(voc)] + base[4:] + ["-i", str(inst),
               "-filter_complex", "amix=inputs=2:normalize=0", "-ac", "2", "-ar", "44100", str(dest)]
        # (-ss/-t must come before each -i; base[4:] repeats them for the second input)
    else:
        cmd = base + ["-i", str(voc), "-ac", "2", "-ar", "44100", str(dest)]
    subprocess.run(cmd, check=True)


def analyze(out_dir):
    """Lead vs backing loudness, second by second. Returns None if it cannot be worked out."""
    try:
        import numpy as np
        import soundfile as sf
    except Exception:
        return None
    files = [p for p in out_dir.glob("*.wav")]
    lead = next((p for p in files if any(k in p.name.lower() for k in ("(vocals)", "(karaoke)", "(lead)"))), None)
    back = next((p for p in files if p != lead), None)
    if not lead or not back:
        return None
    a, sr = sf.read(str(lead), always_2d=True)
    b, _ = sf.read(str(back), always_2d=True)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    db = lambda x: 20 * np.log10(float(np.sqrt(np.mean(x ** 2))) + 1e-9)
    secs = n // sr
    louder = sum(1 for i in range(secs) if db(a[i * sr:(i + 1) * sr]) > db(b[i * sr:(i + 1) * sr]))
    return {"lead_db": db(a), "back_db": db(b), "louder": 100.0 * louder / max(secs, 1)}


def run_one(model, precision, clip, out_dir, clip_seconds):
    import torch
    from audio_separator.separator import Separator

    extra = {}
    if precision == "autocast":
        extra["use_autocast"] = True
    elif precision == "fp16":
        extra["use_native_fp16"] = True
    out_dir.mkdir(parents=True, exist_ok=True)
    cuda = torch.cuda.is_available()
    if cuda:
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    sep = Separator(output_dir=str(out_dir), output_format="WAV", model_file_dir=str(kk.MODEL_DIR),
                    log_level=logging.WARNING, **extra)
    sep.load_model(model_filename=model)
    t_load = time.time() - t0
    device = str(getattr(sep, "torch_device", "?"))
    t1 = time.time()
    sep.separate(str(clip))
    t_run = time.time() - t1
    peak = total = 0.0
    if cuda:
        peak = torch.cuda.max_memory_allocated() / 2 ** 30
        total = torch.cuda.get_device_properties(0).total_memory / 2 ** 30
    del sep
    gc.collect()
    if cuda:
        torch.cuda.empty_cache()
    return {"model": model, "precision": precision, "device": device, "load": t_load, "run": t_run,
            "per_song": t_run * 210.0 / clip_seconds, "peak": peak, "total": total,
            "split": analyze(out_dir)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song")
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--start", type=int, default=40)
    ap.add_argument("--precision", default="fp32,autocast,fp16")
    ap.add_argument("--models", default="")
    ap.add_argument("--input", default="vocals", choices=["vocals", "mix"])
    args = ap.parse_args()

    song = Path(args.song)
    if not song.is_dir():
        sys.exit(f"[!] Not a folder: {song}")
    import torch
    print(f"PyTorch {torch.__version__}, GPU available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        total = torch.cuda.get_device_properties(0).total_memory / 2 ** 30
        print(f"GPU: {torch.cuda.get_device_name(0)}, {total:.1f} GB memory")

    work = song / "bench"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    clip = work / "clip.wav"
    make_clip(song, args.seconds, args.start, clip, args.input)
    print(f"Test clip: {args.seconds} s from {args.start} s of {song.name} (input: {args.input})\n")

    models = [kk.KARAOKE_MODEL]
    if args.models == "all":
        models = MODEL_LIST
    elif args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    precisions = [p.strip() for p in args.precision.split(",") if p.strip()]
    if args.models:   # comparing models: one precision is enough
        precisions = precisions[:1]

    results = []
    for model in models:
        for prec in precisions:
            label = f"{model} [{prec}]"
            print(f"--- {label}")
            try:
                r = run_one(model, prec, clip, work / (model.replace(".ckpt", "")[:40] + "_" + prec + "_" + args.input), args.seconds)
                results.append(r)
                print(f"    done in {r['run']:.1f} s on {r['device']}")
            except Exception as e:
                print(f"    FAILED: {type(e).__name__}: {e}")
                gc.collect()

    print("\n==================== RESULT ====================")
    print(f"input: {args.input}")
    print(f"{'model':40} {'mode':9} {'clip s':>6} {'song':>6} {'GB':>5}  {'lead dB':>7} {'back dB':>7} {'lead louder':>11}")
    for r in results:
        note = ""
        if r["total"] and r["peak"] > 0.9 * r["total"]:
            note = "  <- GPU memory nearly full: slow"
        sp = r["split"]
        if sp:
            tail = f"{sp['lead_db']:7.1f} {sp['back_db']:7.1f} {sp['louder']:10.0f}%"
        else:
            tail = f"{'?':>7} {'?':>7} {'?':>11}"
        print(f"{r['model'][:40]:40} {r['precision']:9} {r['run']:6.1f} {r['per_song'] / 60:5.1f}m "
              f"{r['peak']:5.2f}  {tail}{note}")
    print(f"\nListen to the results here (lead = '(vocals)' file, ad-libs = '(instrumental)' file):\n  {work}")


if __name__ == "__main__":
    main()
