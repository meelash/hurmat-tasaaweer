#!/usr/bin/env bash
# Download ncnn's prebuilt Android libraries (arm64 only) into third_party/.
# Same release as the desktop's pip ncnn, so the same model files work.
set -euo pipefail
cd "$(dirname "$0")"
VER=20260526
[ -d third_party/ncnn/arm64-v8a ] && exit 0
tmp=$(mktemp -d)
curl -sSL -o "$tmp/ncnn.zip" "https://github.com/Tencent/ncnn/releases/download/$VER/ncnn-$VER-android-vulkan.zip"
unzip -q "$tmp/ncnn.zip" "ncnn-$VER-android-vulkan/arm64-v8a/*" -d "$tmp"
mkdir -p third_party/ncnn
mv "$tmp/ncnn-$VER-android-vulkan/arm64-v8a" third_party/ncnn/
rm -rf "$tmp"
