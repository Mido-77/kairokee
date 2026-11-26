"""Kairokee setup check for AMD / Intel graphics (Windows): is separating the voice on the graphics chip
(DirectML) really faster than on the processor here? Integrated chips differ a lot, so it is measured.
Prints one line:  KAIROKEE_DMLBENCH {"dml_s": ..., "cpu_s": ..., "use": true/false, "error": ...}
Run by the launcher with KAIROKEE_DATA set (the models are downloaded there, once)."""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
res = {"dml_s": None, "cpu_s": None, "use": False, "error": None}
try:
    import numpy as np
    import soundfile as sf

    import kairokee as kk

    tmp = Path(tempfile.mkdtemp(prefix="kairokee-bench-"))
    try:
        # 20 seconds of "music": a few tones and some noise (only the time matters, not the result)
        sr, sec = 44100, 20
        t = np.arange(sr * sec) / sr
        x = 0.2 * np.sin(2 * np.pi * 220 * t) + 0.1 * np.sin(2 * np.pi * 330 * t) + 0.02 * np.random.randn(len(t))
        clip = tmp / "bench.wav"
        sf.write(str(clip), np.stack([x, x], axis=1).astype("float32"), sr)

        # the first run downloads the models: do that before timing
        warm = tmp / "warm"
        warm.mkdir()
        kk.mdx_worker([str(clip), str(warm), "dml"])
        os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
        from demucs.pretrained import get_model
        get_model("htdemucs")

        out = tmp / "dml"
        out.mkdir()
        t0 = time.time()
        kk.mdx_worker([str(clip), str(out), "dml"])
        res["dml_s"] = round(time.time() - t0, 1)

        out = tmp / "cpu"
        out.mkdir()
        t0 = time.time()
        kk.demucs_worker([str(clip), str(out), "cpu", ""])   # what the processor would do instead
        res["cpu_s"] = round(time.time() - t0, 1)
        res["use"] = res["dml_s"] < 0.75 * res["cpu_s"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
except Exception as e:  # DirectML not working on this computer: stay on the processor
    res["error"] = f"{type(e).__name__}: {e}"

print("KAIROKEE_DMLBENCH " + json.dumps(res), flush=True)
os._exit(0)   # do not wait for the graphics driver to shut down
