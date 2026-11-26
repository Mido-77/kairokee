# Kairokee

Karaoke from any song: search a song, Kairokee removes the singer, keeps the music, and shows
the lyrics in time with the music. The singer and the ad-libs can be turned up or down.
Everything runs locally; nothing is uploaded.

Runs on **Windows 10/11** (fast with an NVIDIA card from GTX 900 to RTX 50, otherwise on the CPU)
and on **Macs with Apple Silicon**, macOS 14+ (uses Apple graphics / Metal).

## Downloads

The ready-to-use zips are attached to the [latest release](../../releases/latest):
`Kairokee-Windows.zip` and `Kairokee-Mac.zip`. User instructions are in
[README-Windows.txt](README-Windows.txt) and [README-Mac.txt](README-Mac.txt) (each zip ships its own as `README.txt`).

## Layout

| Path | What |
|---|---|
| `app/kairokee.py` | The pipeline: YouTube search/download (yt-dlp), vocal separation (Demucs), lead/ad-lib split (audio-separator), lyrics (LRCLIB, or Whisper from the audio), cover art |
| `app/server.py` | Local web server (FastAPI) and job handling |
| `app/static/index.html` | The web page / player |
| `setup/launch.ps1`, `Kairokee*.bat` | Windows launcher and first-run setup |
| `setup/launch.sh`, `Kairokee*.command` | Mac launcher and first-run setup |
| `setup/requirements.txt`, `setup/constraints.txt` | Python packages (shared by both systems) |
| `setup/selftest.py` | Checks PyTorch and the graphics chip after setup |
| `tools/build_release.py` | Builds both zips |
| `tools/bench_split.py` | Speed/quality benchmark for the ad-lib splitter |

## Making a new version

1. Raise `APP_VERSION` in `app/kairokee.py`.
2. Optional: run the automatic install test (Actions tab > "Install and test" > Run workflow).
3. `python tools/build_release.py` and publish a GitHub release with the two zips.
4. `python tools/publish_update.py "What changed" "..."` - everyone's Kairokee then shows a notice that a
   new version is out (read from a secret gist; the notice says to ask Mido for the new zip).

## Building the zips

```
python tools/build_release.py
```

This writes `Kairokee-Windows.zip` and `Kairokee-Mac.zip` next to this folder, with the right line
endings and the executable permission the Mac `.command` files need. Don't zip by hand on Windows.

For personal use only, with music you are allowed to use.
