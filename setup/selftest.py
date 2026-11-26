"""Kairokee self-test: prints one line of JSON describing what works on this PC."""
import json

res = {"torch": None, "cuda_build": None, "cuda_available": False, "gpu": None,
       "gpu_test": "not run", "torch_error": None, "problems": []}
try:
    import torch
    res["torch"] = torch.__version__
    res["cuda_build"] = torch.version.cuda
    res["cuda_available"] = bool(torch.cuda.is_available())
    if res["cuda_available"]:
        res["gpu"] = torch.cuda.get_device_name(0)
        try:
            x = torch.randn(256, 256, device="cuda")
            float((x @ x).sum().item())
            res["gpu_test"] = "ok"
        except Exception as e:  # the card exists but cannot run this PyTorch build
            res["gpu_test"] = f"failed: {type(e).__name__}: {e}"
except Exception as e:
    res["torch_error"] = f"{type(e).__name__}: {e}"

for name in ("demucs", "yt_dlp", "fastapi", "uvicorn", "soundfile", "syncedlyrics",
             "audio_separator", "faster_whisper"):
    try:
        __import__(name)
    except Exception as e:
        res["problems"].append(f"{name}: {type(e).__name__}: {e}")

print("KAIROKEE_SELFTEST " + json.dumps(res))
