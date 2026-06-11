#!/bin/bash

# Find the latest training run directory
LATEST_RUN=$(ls -td runs/segment/train* | head -1)

if [ -z "$LATEST_RUN" ]; then
    echo "No training runs found in runs/segment/"
    exit 1
fi

BEST_WEIGHTS="$LATEST_RUN/weights/best.pt"

if [ ! -f "$BEST_WEIGHTS" ]; then
    echo "Could not find best.pt in $LATEST_RUN/weights/"
    exit 1
fi

DEST_DIR="server/data/weights/page_detector"
mkdir -p "$DEST_DIR"

echo "Copying $BEST_WEIGHTS to $DEST_DIR/best.pt"
cp "$BEST_WEIGHTS" "$DEST_DIR/best.pt"

echo "Done! The server will now use the custom YOLO model on next startup."
