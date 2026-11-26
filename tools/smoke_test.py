"""
Kairokee smoke test: start the app from an installed Kairokee and use every main feature once,
with a test song made on the spot (synthetic music + an optional spoken "voice" file).

    <kairokee home>/venv/.../python tools/smoke_test.py --home <kairokee home> [--voice voice.wav] [--split]

Used by the automatic GitHub tests (.github/workflows/test.yml). Exit code 0 = everything worked.
It uses its own data folder (<home>/smoke-data), so real songs are never touched.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, raw=None, timeout=600):
    data, headers = None, {}
    if body is not None:
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
    elif raw is not None:
        data = raw
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def wait_job(job, limit_min):
    t0 = time.time()
    last = None
    while time.time() - t0 < limit_min * 60:
        _, j = call("GET", f"/api/job/{job}")
        now = (j["state"], j["stage"], j.get("detail"))
        if now != last:
            print(f"   [{int(time.time() - t0):4d}s] {j['state']} / {j['stage']} {j.get('detail') or ''}", flush=True)
            last = now
        if j["state"] != "running":
            return j
        time.sleep(3)
    raise SystemExit(f"FAIL: job did not finish in {limit_min} minutes")


def check(ok, what):
    print(("  ok   " if ok else "  FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True, help="the Kairokee folder made by the launcher")
    ap.add_argument("--voice", help="a spoken sentence (wav/aiff) to mix into the test song")
    ap.add_argument("--split", action="store_true", help="also test the singer / ad-lib split (slow on a CPU)")
    ap.add_argument("--seconds", type=int, default=25)
    args = ap.parse_args()

    home = Path(args.home).resolve()
    data = home / "smoke-data"
    shutil.rmtree(data, ignore_errors=True)
    (data / "output").mkdir(parents=True)
    env = dict(os.environ)
    extra = [str(home / "ffmpeg"), str(home / "deno")]
    env["PATH"] = os.pathsep.join([p for p in extra if Path(p).exists()] + [env.get("PATH", "")])
    env.update(KAIROKEE_DATA=str(data), KAIROKEE_PORT=str(PORT), KAIROKEE_WHISPER_MODEL="small",
               PYTHONUTF8="1", PYTHONUNBUFFERED="1", PYTORCH_ENABLE_MPS_FALLBACK="1")
    # keep the downloaded AI models between runs of the same installation
    models = home / "data" / "models"
    models.mkdir(parents=True, exist_ok=True)
    env["KAIROKEE_MODELS"] = str(models)
    ffmpeg = shutil.which("ffmpeg", path=env["PATH"])
    check(bool(ffmpeg), f"ffmpeg found ({ffmpeg})")

    # ---- the test song: two-tone "music" + the spoken voice in the middle
    song = data / "Kairokee Test - Smoke Song.wav"
    sec = args.seconds
    inputs = ["-f", "lavfi", "-i", f"sine=frequency=196:duration={sec}",
              "-f", "lavfi", "-i", f"sine=frequency=294:duration={sec}"]
    graph = "[0][1]amix=inputs=2,volume=0.6[m]"
    if args.voice:
        inputs += ["-i", args.voice]
        graph += ";[2]adelay=3000|3000,volume=2[v];[m][v]amix=inputs=2:duration=first:normalize=0[out]"
    else:
        graph = graph.replace("[m]", "[out]")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
                    "-ac", "2", "-ar", "44100", str(song)], check=True)

    server = subprocess.Popen([sys.executable, str(ROOT / "app" / "server.py")], cwd=str(ROOT / "app"), env=env)
    try:
        t0 = time.time()
        while time.time() - t0 < 180:
            try:
                status, cfg = call("GET", "/api/config", timeout=5)
                if status == 200:
                    break
            except Exception:
                time.sleep(2)
        else:
            raise SystemExit("FAIL: the server did not start")
        print(f"\nServer is up. Device: {cfg['device']} ({cfg.get('gpu')})\n", flush=True)

        print("1. Add a song from a file", flush=True)
        status, up = call("PUT", "/api/upload?name=" + urllib.parse.quote(song.name), raw=song.read_bytes())
        check(status == 200 and up.get("upload"), f"upload accepted ({up})")
        status, r = call("POST", "/api/process", {"title": up["title"], "duration": up["duration"],
                                                   "split": args.split, "upload": up["upload"]})
        check(status == 200, "processing started")
        j = wait_job(r["job"], 45)
        check(j["state"] == "done", f"song made ({j.get('error') or 'no error'})")
        slug = j["slug"]
        _, s = call("GET", "/api/song/" + urllib.parse.quote(slug))
        check(s["hasVocals"], "vocals track saved")
        if args.split:
            check(s["hasLead"], "singer and ad-libs split")

        if args.voice:
            print("\n2. Write lyrics from the audio (Whisper)", flush=True)
            status, r = call("POST", f"/api/song/{urllib.parse.quote(slug)}/transcribe", {"language": "en"})
            check(status == 200, "transcription started")
            j = wait_job(r["job"], 30)
            check(j["state"] == "done", f"lyrics written ({j.get('error') or 'no error'})")
            _, s = call("GET", "/api/song/" + urllib.parse.quote(slug))
            print("     " + s["lyrics"].strip().replace("\n", "\n     "))
            check("<" in s["lyrics"] and "]" in s["lyrics"], "lyrics have line and word times")

        print("\n3. Change the key", flush=True)
        status, r = call("POST", f"/api/song/{urllib.parse.quote(slug)}/key", {"semitones": -2})
        check(status == 200 and r["path"] == "key/-2/", f"key changed ({r})")
        url = f"{BASE}/files/{urllib.parse.quote(slug)}/key/-2/instrumental.mp3"
        with urllib.request.urlopen(url, timeout=60) as resp:
            check(resp.status == 200 and len(resp.read()) > 10000, "music in the new key can be played")

        print("\n4. Save a recording", flush=True)
        voice = Path(args.voice) if args.voice else song
        status, r = call("PUT", f"/api/song/{urllib.parse.quote(slug)}/recording?start=1&latency=0.05&music=0.8&key=-2"
                                f"&ext={voice.suffix.lstrip('.') or 'wav'}", raw=voice.read_bytes())
        check(status == 200, f"recording saved ({r})")

        print("\n5. Delete and undo", flush=True)
        status, r = call("DELETE", "/api/song/" + urllib.parse.quote(slug))
        check(status == 200, "song deleted")
        _, lib = call("GET", "/api/library")
        check(all(x["slug"] != slug for x in lib), "song gone from the library")
        status, r = call("POST", f"/api/trash/{urllib.parse.quote(r['trash'])}/restore")
        _, lib = call("GET", "/api/library")
        check(status == 200 and any(x["slug"] == slug for x in lib), "song is back after undo")
        print("\nALL GOOD", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except Exception:
            server.kill()


if __name__ == "__main__":
    main()
