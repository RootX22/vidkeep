#!/bin/sh
# Build Vidkeep.app (Apple silicon and Intel) into macos/build/.
# usage: macos/build.sh [--standalone] [--install]
#   --standalone  put vidkeep, ffmpeg and deno inside the app (the downloadable build; needs python3)
#   --install     also copy it to ~/Applications
set -eu
cd "$(dirname "$0")"

STANDALONE=no
INSTALL=no
for flag in "$@"; do
    case "$flag" in
        --standalone) STANDALONE=yes ;;
        --install) INSTALL=yes ;;
        *) echo "unknown option $flag" >&2; exit 2 ;;
    esac
done

BUILD=build
APP="$BUILD/Vidkeep.app"
rm -rf "${APP:?}"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

for arch in arm64 x86_64; do
    swiftc -O -swift-version 5 -parse-as-library \
        -target "$arch-apple-macos14.0" \
        -o "$BUILD/Vidkeep-$arch" Sources/*.swift
done
lipo -create -output "$APP/Contents/MacOS/Vidkeep" "$BUILD/Vidkeep-arm64" "$BUILD/Vidkeep-x86_64"
cp Info.plist "$APP/Contents/Info.plist"

ICONSET="$BUILD/AppIcon.iconset"
mkdir -p "$ICONSET"
swift Icon/make_icon.swift "$BUILD/icon-1024.png"
for points in 16 32 128 256 512; do
    sips -z "$points" "$points" "$BUILD/icon-1024.png" --out "$ICONSET/icon_${points}x${points}.png" >/dev/null
    double=$((points * 2))
    sips -z "$double" "$double" "$BUILD/icon-1024.png" --out "$ICONSET/icon_${points}x${points}@2x.png" >/dev/null
done
iconutil -c icns -o "$APP/Contents/Resources/AppIcon.icns" "$ICONSET"

if [ "$STANDALONE" = yes ]; then
    ../packaging/macos/bundle_cli.sh "$APP" "${PYTHON:-python3}"
fi

# Ad-hoc signature, nested programs included: enough for an app built on this Mac to open
# without warnings; a downloaded copy needs "Open" from the right-click menu the first time.
codesign --force --deep --sign - "$APP"
echo "built $(pwd)/$APP"

if [ "$INSTALL" = yes ]; then
    mkdir -p "$HOME/Applications"
    rm -rf "${HOME:?}/Applications/Vidkeep.app"
    cp -R "$APP" "$HOME/Applications/"
    echo "installed $HOME/Applications/Vidkeep.app"
fi
