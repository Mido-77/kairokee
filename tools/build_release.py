"""
Build the two download files, next to this source folder:

    ../Kairokee-Windows.zip
    ../Kairokee-Mac.zip

Run from anywhere:   python tools/build_release.py   (it replaces the old zips)

Why a script: a ZIP made by Windows' "Send to > Compressed folder" loses the "executable" mark
that macOS needs to double-click a .command file, and may give the Mac scripts Windows line endings
(which make bash fail). This script writes every file with the right line endings and permissions.
"""
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT.parent   # the zips go next to the source folder, not inside it

SHARED = [
    "app/kairokee.py",
    "app/server.py",
    "app/static/index.html",
    "setup/requirements.txt",
    "setup/constraints.txt",
    "setup/selftest.py",
    "tools/bench_split.py",
]
WINDOWS = SHARED + [
    ("README-Windows.txt", "README.txt"),
    "Kairokee.bat",
    "Kairokee-Phone.bat",
    "Kairokee-Repair.bat",
    "setup/launch.ps1",
]
MAC = SHARED + [
    ("README-Mac.txt", "README.txt"),
    "Kairokee.command",
    "Kairokee-Phone.command",
    "Kairokee-Repair.command",
    "setup/launch.sh",
]
EXECUTABLE = (".command", ".sh")


def file_bytes(src, crlf):
    data = (ROOT / src).read_bytes()
    data = data.replace(b"\r\n", b"\n")
    if crlf:
        data = data.replace(b"\n", b"\r\n")
    return data


def build(name, files, crlf_for):
    DIST.mkdir(exist_ok=True)
    out = DIST / name
    stamp = time.localtime()[:6]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in files:
            src, dst = item if isinstance(item, tuple) else (item, item)
            if not (ROOT / src).exists():
                raise SystemExit(f"[!] missing file: {src}")
            info = zipfile.ZipInfo("Kairokee/" + dst, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3   # "made on Unix": macOS then reads the permissions below
            mode = 0o755 if dst.endswith(EXECUTABLE) else 0o644
            info.external_attr = (0o100000 | mode) << 16
            crlf = dst.endswith(crlf_for)
            z.writestr(info, file_bytes(src, crlf))
    print(f"  {out}  ({out.stat().st_size // 1024} KB, {len(files)} files)")


if __name__ == "__main__":
    print("Building:")
    build("Kairokee-Windows.zip", WINDOWS, crlf_for=(".bat", ".ps1", ".txt"))
    build("Kairokee-Mac.zip", MAC, crlf_for=())
