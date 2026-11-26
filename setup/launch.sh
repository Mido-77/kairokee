#!/bin/bash
# Kairokee - launcher and first-run setup for Macs with Apple Silicon (M1 or newer), macOS 14 or newer.
# Written for the old bash 3.2 that comes with macOS. Keep this file plain ASCII.
#   launch.sh            start Kairokee (sets everything up the first time)
#   launch.sh --phone    let phones on the same Wi-Fi open the app
#   launch.sh --repair   throw away the installed packages and install them again

set -u

PHONE=0
REPAIR=0
for a in ${1+"$@"}; do   # (this odd form keeps the old macOS bash happy when there are no arguments)
    case "$a" in
        --phone) PHONE=1 ;;
        --repair) REPAIR=1 ;;
    esac
done

# ----------------------------------------------------------------------------- settings
SETUP_VERSION=2              # raise this when a new app version needs a fresh setup
FIRST_PORT=8000              # if it is taken, the next free one up to LAST_PORT is used
LAST_PORT=8010
TORCH_VERSION=2.14.1         # Mac builds come from pypi.org and include Apple graphics (Metal / MPS) support
TORCHAUDIO_VERSION=2.11.0
TORCHVISION_VERSION=0.29.1
MIN_MACOS=14                 # PyTorch 2.14 needs macOS 14 Sonoma or newer
MIN_FREE_GB=12
MIN_RAM_GB=8
# Fixed versions with checksums: a broken or changed upstream release cannot break new installs.
UV_URL="https://github.com/astral-sh/uv/releases/download/0.13.9/uv-aarch64-apple-darwin.tar.gz"
UV_SHA="5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca"
DENO_URL="https://github.com/denoland/deno/releases/download/v2.9.8/deno-aarch64-apple-darwin.zip"
DENO_SHA="9f0bf660393da2eaff044cdc7444cb08c73f235849b61d224d825a194ce7c98e"
FFMPEG_BASE="https://ffmpeg.martin-riedl.de/download/macos/arm64/1795207104_9.1"
FFMPEG_SHA="1f66e9a690a7f1c9e60b012379f8869c11818d1797f45b21dbb2f2564ba61ae1"
FFPROBE_SHA="6ef2df5f575042923a28537ab37adf936f28bf3e7b85de23a1447ed447ec1dbd"

SETUP_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SETUP_DIR")"
APP_DIR="$ROOT/app"

KHOME="${KAIROKEE_HOME:-$HOME/Library/Application Support/Kairokee}"
TOOLS_DIR="$KHOME/tools"
UV="$TOOLS_DIR/uv"
VENV_DIR="$KHOME/venv"
VENV_PY="$VENV_DIR/bin/python"
FFMPEG_DIR="$KHOME/ffmpeg"
DENO_DIR="$KHOME/deno"
DATA_DIR="$KHOME/data"
LOG_DIR="$KHOME/logs"
STATE_FILE="$KHOME/state.txt"

export UV_PYTHON_INSTALL_DIR="$KHOME/python"
export UV_CACHE_DIR="$KHOME/cache/uv"
export UV_PYTHON_PREFERENCE=only-managed
export UV_HTTP_TIMEOUT=300

# ----------------------------------------------------------------------------- small helpers
if [ -t 1 ]; then C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_OFF=$'\033[0m'
else C_CYAN=; C_GREEN=; C_YELLOW=; C_RED=; C_OFF=; fi
title() { echo; echo "${C_CYAN}$1${C_OFF}"; }
ok()    { echo "    ${C_GREEN}OK: $1${C_OFF}"; }
note()  { echo "    ${C_YELLOW}NOTE: $1${C_OFF}"; }
info()  { echo "    $1"; }

LOG_FILE=""
fail() {
    echo
    echo "  ${C_RED}Kairokee could not start.${C_OFF}"
    echo "  ${C_RED}$1${C_OFF}"
    [ -n "$LOG_FILE" ] && echo "  ${C_YELLOW}Details were saved here: $LOG_FILE${C_OFF}"
    echo
    exit 1
}

# run a command; try again a few times (a weak internet connection is the usual reason it fails)
run() {
    local what="$1" tries="$2"; shift 2
    local i=1
    while [ "$i" -le "$tries" ]; do
        "$@" && return 0
        if [ "$i" -lt "$tries" ]; then note "$what did not finish (try $i of $tries). Trying again..."; sleep 3; fi
        i=$((i + 1))
    done
    fail "$what failed. Scroll up to read the error. A weak internet connection is the usual cause: just open Kairokee again and it will continue."
}

# download URL to FILE and check its SHA-256 checksum
get_file() {
    local url="$1" dest="$2" sha="$3"
    rm -f "$dest.part"
    if ! curl -fL --retry 3 --connect-timeout 30 -o "$dest.part" "$url"; then
        rm -f "$dest.part"; return 1
    fi
    local got
    got="$(shasum -a 256 "$dest.part" | awk '{print $1}')"
    if [ "$got" != "$sha" ]; then
        rm -f "$dest.part"
        echo "    The download from $url was damaged or changed (checksum does not match)."
        return 1
    fi
    mv -f "$dest.part" "$dest"
}

state_get() { [ -f "$STATE_FILE" ] && grep "^$1=" "$STATE_FILE" | head -n 1 | cut -d= -f2-; }
state_set() {
    local tmp="$STATE_FILE.tmp"
    { [ -f "$STATE_FILE" ] && grep -v "^$1=" "$STATE_FILE"; echo "$1=$2"; } > "$tmp"
    mv -f "$tmp" "$STATE_FILE"
}

# ----------------------------------------------------------------------------- setup (first run)
install_torch() {
    run 'Installing PyTorch (the biggest download)' 3 \
        "$UV" pip install --python "$VENV_PY" "torch==$TORCH_VERSION" "torchaudio==$TORCHAUDIO_VERSION" "torchvision==$TORCHVISION_VERSION"
}

selftest() { "$VENV_PY" "$SETUP_DIR/selftest.py" 2>&1 | grep '^KAIROKEE_SELFTEST ' | head -n 1 | cut -c19-; }

# read one field from the self-test JSON (python from the new environment does the parsing)
json_field() { printf '%s' "$1" | "$VENV_PY" -c 'import json,sys
d=json.loads(sys.stdin.read() or "{}"); v=d.get(sys.argv[1])
print("" if v is None else ("\n".join(v) if isinstance(v,list) else v))' "$2" 2>/dev/null; }

setup() {
    local total=7

    title "[1/$total] Checking this Mac"
    local macos major
    macos="$(sw_vers -productVersion 2>/dev/null)"
    major="${macos%%.*}"
    if [ -z "$major" ] || [ "$major" -lt "$MIN_MACOS" ]; then
        fail "Kairokee needs macOS $MIN_MACOS (Sonoma) or newer. This Mac has macOS $macos. Update it in System Settings > General > Software Update."
    fi
    ok "macOS $macos"

    mkdir -p "$KHOME" || fail "Could not create the folder $KHOME"
    local free_gb
    free_gb=$(( $(df -k "$KHOME" | awk 'NR==2 {print $4}') / 1048576 ))
    if [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
        fail "Not enough free disk space: $free_gb GB free, about $MIN_FREE_GB GB needed. Free some space and open Kairokee again."
    fi
    ok "$free_gb GB free"

    local ram_gb
    ram_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
    if [ "$ram_gb" -lt "$MIN_RAM_GB" ]; then
        fail "This Mac has only $ram_gb GB of memory. Kairokee needs at least $MIN_RAM_GB GB."
    fi
    ok "$ram_gb GB of memory"

    if ! curl -fsI --connect-timeout 15 https://pypi.org/simple/ >/dev/null; then
        fail "No internet connection. The first setup needs the internet (about 3 GB). Connect and open Kairokee again."
    fi
    ok "Internet works"
    local chip
    chip="$(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
    ok "${chip:-Apple Silicon} -> fast mode with Apple graphics (Metal)"

    title "[2/$total] Getting the installer tool (uv)"
    if [ ! -x "$UV" ]; then
        mkdir -p "$TOOLS_DIR"
        info "Downloading..."
        get_file "$UV_URL" "$TOOLS_DIR/uv.tar.gz" "$UV_SHA" || fail "Could not download the installer tool (uv). Check the internet and open Kairokee again."
        tar -xzf "$TOOLS_DIR/uv.tar.gz" -C "$TOOLS_DIR" || fail "Could not unpack the installer tool (uv)."
        rm -f "$TOOLS_DIR/uv.tar.gz"
        local found
        found="$(find "$TOOLS_DIR" -type f -name uv -perm -u+x | head -n 1)"
        [ -n "$found" ] && [ "$found" != "$UV" ] && cp "$found" "$UV"
        [ -x "$UV" ] || fail "Could not set up the installer tool (uv)."
    fi
    ok "Ready"

    title "[3/$total] Getting Python (a private copy, nothing else on your Mac is touched)"
    run 'Installing Python' 3 "$UV" python install 3.11 --no-bin
    rm -rf "$VENV_DIR"
    run 'Creating the Kairokee environment' 1 "$UV" venv "$VENV_DIR" --python 3.11
    ok "Ready"

    title "[4/$total] Installing PyTorch - this is the big one, please wait"
    install_torch
    ok "Installed"

    title "[5/$total] Installing the other parts"
    run 'Installing the other parts' 3 \
        "$UV" pip install --python "$VENV_PY" -r "$SETUP_DIR/requirements.txt" -c "$SETUP_DIR/constraints.txt"
    ok "Installed"

    title "[6/$total] Getting ffmpeg (audio tool)"
    if [ -x "$FFMPEG_DIR/ffmpeg" ] && [ -x "$FFMPEG_DIR/ffprobe" ]; then
        ok "Ready"
    elif command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
        ok "ffmpeg is already installed on this Mac ($(command -v ffmpeg))"
    else
        local tmp="$KHOME/cache/ffmpeg-tmp"
        rm -rf "$tmp"; mkdir -p "$tmp/x" "$FFMPEG_DIR"
        info "Downloading from ffmpeg.martin-riedl.de ..."
        get_file "$FFMPEG_BASE/ffmpeg.zip" "$tmp/ffmpeg.zip" "$FFMPEG_SHA" &&
            get_file "$FFMPEG_BASE/ffprobe.zip" "$tmp/ffprobe.zip" "$FFPROBE_SHA" ||
            fail "Could not download ffmpeg. Check the internet connection and open Kairokee again. (If you use Homebrew, 'brew install ffmpeg' also works.)"
        unzip -oq "$tmp/ffmpeg.zip" -d "$tmp/x" && unzip -oq "$tmp/ffprobe.zip" -d "$tmp/x" || fail "Could not unpack ffmpeg."
        local ff fp
        ff="$(find "$tmp/x" -type f -name ffmpeg | head -n 1)"
        fp="$(find "$tmp/x" -type f -name ffprobe | head -n 1)"
        [ -n "$ff" ] && [ -n "$fp" ] || fail "ffmpeg was not found after unpacking."
        cp -f "$ff" "$FFMPEG_DIR/ffmpeg"; cp -f "$fp" "$FFMPEG_DIR/ffprobe"
        chmod +x "$FFMPEG_DIR/ffmpeg" "$FFMPEG_DIR/ffprobe"
        xattr -d com.apple.quarantine "$FFMPEG_DIR/ffmpeg" "$FFMPEG_DIR/ffprobe" 2>/dev/null
        rm -rf "$tmp"
        "$FFMPEG_DIR/ffmpeg" -hide_banner -version >/dev/null 2>&1 || fail "The downloaded ffmpeg does not run on this Mac."
        ok "Ready"
    fi

    title "[7/$total] Testing everything"
    local t err device problems
    t="$(selftest)"
    err="$(json_field "$t" torch_error)"
    [ -z "$t" ] && fail "The test program printed nothing useful. Open Kairokee-Repair.command."
    [ -n "$err" ] && fail "PyTorch does not start on this Mac: $err"
    problems="$(json_field "$t" problems)"
    if [ -n "$problems" ]; then
        echo "$problems" | while IFS= read -r p; do echo "    ${C_RED}PROBLEM: $p${C_OFF}"; done
        fail "Some parts did not install correctly. Open Kairokee-Repair.command. If it happens again, send the log file to Mido."
    fi
    device=cpu
    if [ "$(json_field "$t" mps_test)" = "ok" ]; then
        device=mps
        ok "Apple graphics (Metal) works"
    else
        note "Apple graphics (Metal) did not pass the test ($(json_field "$t" mps_test)). Kairokee will use the processor: slower, but it works."
    fi
    ok "PyTorch $(json_field "$t" torch), mode: $(echo "$device" | tr '[:lower:]' '[:upper:]')"

    # Lyrics model: it runs on the processor on a Mac (fast on Apple Silicon), so pick it by memory size
    local whisper=small
    [ "$ram_gb" -ge 16 ] && whisper=medium

    : > "$STATE_FILE"
    state_set SetupVersion "$SETUP_VERSION"
    state_set Device "$device"
    state_set GpuName "${chip:-Apple Silicon}"
    state_set Whisper "$whisper"
    state_set LastYtdlpCheck "$(date +%s)"
    echo
    echo "${C_GREEN}Setup finished. You will not see this part again.${C_OFF}"
}

# ----------------------------------------------------------------------------- start the app
update_ytdlp() {
    # YouTube changes often and yt-dlp must follow. Check at most once a day; never block on failure.
    local last now
    last="$(state_get LastYtdlpCheck)"; now="$(date +%s)"
    case "$last" in ''|*[!0-9]*) last=0 ;; esac
    [ $((now - last)) -lt 86400 ] && return 0
    info "Checking for a newer yt-dlp (the YouTube downloader)..."
    if "$UV" pip install --python "$VENV_PY" --upgrade-package yt-dlp --quiet 'yt-dlp[default]' >/dev/null 2>&1; then
        state_set LastYtdlpCheck "$now"
    fi
}

confirm_youtube_helpers() {
    # Since late 2025 YouTube needs two helpers next to yt-dlp: the "yt-dlp-ejs" package and a
    # JavaScript runtime (Deno). This installs whichever is missing (fast when everything is there).
    if ! "$UV" pip show --python "$VENV_PY" yt-dlp-ejs >/dev/null 2>&1; then
        info "Installing the YouTube helper package (yt-dlp-ejs)..."
        "$UV" pip install --python "$VENV_PY" 'yt-dlp[default]' ||
            note "Could not install the YouTube helper package. Downloading songs may fail until this works; open Kairokee again with the internet on."
    fi
    if [ ! -x "$DENO_DIR/deno" ]; then
        info "Getting Deno (a small program that YouTube downloads need)..."
        mkdir -p "$DENO_DIR"
        if get_file "$DENO_URL" "$DENO_DIR/deno.zip" "$DENO_SHA" && unzip -oq "$DENO_DIR/deno.zip" -d "$DENO_DIR"; then
            chmod +x "$DENO_DIR/deno"
            xattr -d com.apple.quarantine "$DENO_DIR/deno" 2>/dev/null
        else
            note "Could not set up Deno. Downloading songs may fail until this works; open Kairokee again with the internet on."
        fi
        rm -f "$DENO_DIR/deno.zip"
    fi
}

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
kairokee_on() { curl -fs --max-time 3 "http://127.0.0.1:$1/api/config" 2>/dev/null | grep -q '"perMinute"'; }

SERVER_PID=""
stop_server() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null
        sleep 1
        kill -9 "$SERVER_PID" 2>/dev/null
    fi
}

start_kairokee() {
    local port=0 p
    for p in $(seq "$FIRST_PORT" "$LAST_PORT"); do
        if port_busy "$p"; then
            if kairokee_on "$p"; then
                echo "${C_GREEN}Kairokee is already running. Opening it...${C_OFF}"
                open "http://localhost:$p"
                return 0
            fi
            continue
        fi
        port=$p
        break
    done
    [ "$port" -eq 0 ] && fail "Other programs are using all the ports Kairokee can use ($FIRST_PORT to $LAST_PORT). Restart the Mac and open Kairokee again."
    [ "$port" -ne "$FIRST_PORT" ] && note "Port $FIRST_PORT is used by another program, so Kairokee uses port $port."

    mkdir -p "$DATA_DIR/output"
    export KAIROKEE_PORT="$port"
    export KAIROKEE_DATA="$DATA_DIR"
    KAIROKEE_WHISPER_MODEL="$(state_get Whisper)"
    KAIROKEE_DEVICE="$(state_get Device)"
    KAIROKEE_GPU_NAME="$(state_get GpuName)"
    export KAIROKEE_WHISPER_MODEL KAIROKEE_DEVICE KAIROKEE_GPU_NAME
    export KAIROKEE_SPLIT_PRECISION="${KAIROKEE_SPLIT_PRECISION:-fp32}"
    export PYTORCH_ENABLE_MPS_FALLBACK=1      # the few steps Apple graphics cannot do run on the processor
    export PYTHONUTF8=1
    export PYTHONIOENCODING=utf-8
    export DENO_NO_UPDATE_CHECK=1
    [ -d "$FFMPEG_DIR" ] && export PATH="$FFMPEG_DIR:$PATH"
    [ -d "$DENO_DIR" ] && export PATH="$DENO_DIR:$PATH"

    local mode="fast mode (Apple graphics)"
    [ "$KAIROKEE_DEVICE" = "cpu" ] && mode="processor mode (slower: a song can take many minutes)"
    echo
    echo "  ${C_CYAN}Kairokee${C_OFF}"
    echo "  Mode: $mode"
    echo "  Starting... your browser will open in a moment."
    if [ "$PHONE" -eq 1 ]; then
        echo
        echo "  ${C_YELLOW}PHONE MODE: phones on the same Wi-Fi can open the address shown below.${C_OFF}"
        echo "  ${C_YELLOW}If the Mac asks whether Python may accept incoming network connections, click Allow.${C_OFF}"
    fi
    echo "  To stop Kairokee, close this window (or press Ctrl+C)."

    local args=("$APP_DIR/server.py")
    [ "$PHONE" -eq 1 ] && args+=(--phone)
    (cd "$APP_DIR" && exec "$VENV_PY" "${args[@]}") &
    SERVER_PID=$!
    trap 'stop_server; exit 0' INT TERM HUP
    trap stop_server EXIT

    local i ready=0
    for i in $(seq 1 170); do
        kill -0 "$SERVER_PID" 2>/dev/null || break
        if kairokee_on "$port"; then ready=1; break; fi
        sleep 0.7
    done
    if [ "$ready" -eq 1 ]; then
        open "http://localhost:$port"
    elif ! kill -0 "$SERVER_PID" 2>/dev/null; then
        wait "$SERVER_PID"
        fail "Kairokee stopped right after starting (code $?). Read the message above."
    else
        note "Kairokee is taking long to start. Open http://localhost:$port yourself in a minute."
    fi
    wait "$SERVER_PID"
    local code=$?
    SERVER_PID=""
    [ "$code" -ne 0 ] && fail "Kairokee stopped (code $code). Read the message above."
    return 0
}

# ----------------------------------------------------------------------------- main
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" != "1" ]; then
    fail "This version of Kairokee needs a Mac with Apple Silicon (M1, M2, M3, M4 or newer). Macs with an Intel processor are not supported."
fi
if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = "1" ]; then
    # this Terminal runs through Rosetta (Intel emulation): start again as a native Apple Silicon program
    exec arch -arm64 /bin/bash "$0" ${1+"$@"}
fi

mkdir -p "$LOG_DIR" 2>/dev/null
if [ -d "$LOG_DIR" ]; then
    LOG_FILE="$LOG_DIR/kairokee-$(date +%Y%m%d-%H%M%S).log"
    exec > >(tee -a "$LOG_FILE") 2>&1
    # keep only the 10 newest logs
    ls -1t "$LOG_DIR"/kairokee-*.log 2>/dev/null | tail -n +11 | while IFS= read -r f; do rm -f "$f"; done
fi

[ -f "$APP_DIR/server.py" ] || fail "The app files are missing. Unzip the whole Kairokee ZIP file first, then open Kairokee.command from the new folder."


# The ZIP came from the internet, so macOS marked every file in it. After you allowed Kairokee.command
# once, clear that mark on the Kairokee folder so the Phone and Repair files open without warnings too.
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null

if [ "$REPAIR" -eq 1 ]; then
    echo "${C_YELLOW}Repair: removing the installed parts (your songs and downloaded models are kept)...${C_OFF}"
    rm -rf "$VENV_DIR" "$STATE_FILE"
fi

if [ "$(state_get SetupVersion)" = "$SETUP_VERSION" ] && [ -x "$VENV_PY" ]; then
    update_ytdlp
else
    echo
    echo "  ${C_CYAN}Welcome to Kairokee. First-time setup starts now.${C_OFF}"
    echo "  It downloads about 3 GB once and may take 10-60 minutes. Please keep this window open."
    # keep the Mac awake while setting up (stops by itself when this window closes)
    caffeinate -i -w $$ >/dev/null 2>&1 &
    setup
fi
confirm_youtube_helpers
start_kairokee
