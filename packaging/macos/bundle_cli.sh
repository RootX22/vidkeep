#!/bin/sh
# Put a self-contained vidkeep (frozen with PyInstaller) and its own ffmpeg, ffprobe and deno inside
# Vidkeep.app, so the app people download needs nothing else installed.
# Apple silicon only: the bundled tools are arm64 builds. On an Intel Mac the app falls back to a
# vidkeep installed the usual way.
# usage: packaging/macos/bundle_cli.sh path/to/Vidkeep.app [python3]
set -eu

APP=$1
PYTHON=${2:-python3}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK="$ROOT/build/mac-bundle"

# Pinned downloads, each checked against the hash recorded here.
FFMPEG_URL=https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2
FFMPEG_SHA=c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924
FFPROBE_SHA=fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6
DENO_URL=https://github.com/denoland/deno/releases/download/v2.9.8/deno-aarch64-apple-darwin.zip
DENO_SHA=9f0bf660393da2eaff044cdc7444cb08c73f235849b61d224d825a194ce7c98e

fetch() {  # url file sha256
    curl -fsSL -o "$2" "$1"
    echo "$3  $2" | shasum -a 256 -c - >/dev/null
}

rm -rf "$WORK"
mkdir -p "$WORK"
"$PYTHON" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q "$ROOT" pyinstaller==6.22.3
"$WORK/venv/bin/pyinstaller" --noconfirm --log-level WARN --console --name vidkeep \
    --collect-all yt_dlp_ejs --collect-submodules rich --exclude-module mutagen \
    --distpath "$WORK/dist" --workpath "$WORK/pyinstaller" --specpath "$WORK" \
    "$ROOT/packaging/cli_entry.py"

BIN="$WORK/dist/vidkeep/bin"
mkdir -p "$BIN"
fetch "$FFMPEG_URL/ffmpeg.zip" "$WORK/ffmpeg.zip" "$FFMPEG_SHA"
fetch "$FFMPEG_URL/ffprobe.zip" "$WORK/ffprobe.zip" "$FFPROBE_SHA"
fetch "$DENO_URL" "$WORK/deno.zip" "$DENO_SHA"
for archive in ffmpeg ffprobe deno; do
    unzip -q -o "$WORK/$archive.zip" -d "$BIN"
done
chmod +x "$BIN"/*
cp "$ROOT/packaging/THIRD_PARTY.txt" "$ROOT/LICENSE" "$WORK/dist/vidkeep/"

rm -rf "$APP/Contents/Resources/vidkeep"
cp -R "$WORK/dist/vidkeep" "$APP/Contents/Resources/vidkeep"
"$APP/Contents/Resources/vidkeep/vidkeep" --version
