#!/bin/bash

echo "Starting YOLO segmentation fine-tuning for A4 Document Pages..."

# Activate virtual environment if necessary
# source ../../venv_311/bin/activate

# Fine-tune YOLOv8 segmentation model
yolo segment train \
  model=yolov8n-seg.pt \
  data=training/page_detector/data.yaml \
  imgsz=640 \
  epochs=100 \
  batch=8

# Optional: YOLO11 version
# yolo segment train \
#   model=yolo11n-seg.pt \
#   data=training/page_detector/data.yaml \
#   imgsz=640 \
#   epochs=100 \
#   batch=8

echo "Training complete. Run export_yolo_seg.sh to copy the best weights to the server."
