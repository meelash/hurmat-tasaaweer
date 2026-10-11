#!/usr/bin/env bash
# Export YOLO11 detection models to ncnn. Needs the heavyweight export venv
# (torch + ultralytics); the runtime prototype only needs ncnn.
#
#   tools/export_models.sh            # small-seg (default), nano and small at 640, 960, 1280
#   tools/export_models.sh n 1280     # one model/size
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv-export/bin/yolo ]; then
    python3 -m venv .venv-export
    .venv-export/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    .venv-export/bin/pip install ultralytics
fi

variants=${1:-"s-seg s-pose n-seg n-pose n s"}
sizes=${2:-"640 960 1280"}
mkdir -p models && cd models
for m in $variants; do
    for sz in $sizes; do
        ../.venv-export/bin/yolo export model="yolo11$m.pt" format=ncnn imgsz="$sz"
        dest="yolo11${m}_ncnn_model"
        [ "$sz" = 640 ] || { rm -rf "yolo11${m}_${sz}_ncnn_model"; mv "$dest" "yolo11${m}_${sz}_ncnn_model"; }
    done
done
