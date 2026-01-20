#!/bin/bash
set -eu

# Config
ROOT="/home/aimee/hl_runtime"
BOOK="$ROOT/hl_book"
TEMP="$ROOT/hl_tmp"
SUFFIX="_by_block"

# Mode: "init" (default, for service startup) or "next" (cron job for next hour)
MODE="${1:-init}"

if [ "$MODE" = "init" ]; then
    # === Init Mode ===
    
    # 1. Base directories
    mkdir -p "$TEMP"
    mkdir -p "$ROOT/hl/periodic_abci_states"

    # 2. FIFO pipes
    for pipe in fills order diffs; do
        if [ ! -p "$BOOK/$pipe" ]; then
            rm -f "$BOOK/$pipe"
            mkfifo "$BOOK/$pipe"
        fi
    done

    # 3. Top-level symlinks (TEMP -> BOOK)
    ln -sf "$BOOK/node_fills$SUFFIX" "$TEMP/node_fills$SUFFIX"
    ln -sf "$BOOK/node_order_statuses$SUFFIX" "$TEMP/node_order_statuses$SUFFIX"
    ln -sf "$BOOK/node_raw_book_diffs$SUFFIX" "$TEMP/node_raw_book_diffs$SUFFIX"
    ln -sf "$ROOT/hl/periodic_abci_states" "$TEMP/periodic_abci_states"

    # Target: Current UTC time
    TARGET_DATE_CMD=(date -u)
    
else
    # === Rotation Mode (Cron) ===
    # Target: Next hour UTC
    TARGET_DATE_CMD=(date -u -d '+1 hour')
    
    # Log execution
    echo "[$(date)] Preparing for next hour..." >> /home/aimee/hl_runtime/hl_book/cron_link.log
fi

# === Common Logic: Generate hourly structure ===

# Get target Date/Hour (strip leading zeros, e.g. 05 -> 5)
D=$("${TARGET_DATE_CMD[@]}" +%Y%m%d)
H=$("${TARGET_DATE_CMD[@]}" +%-H)

# Create dirs and link hourly files to root FIFOs
for type in "node_fills${SUFFIX}:fills" "node_order_statuses${SUFFIX}:order" "node_raw_book_diffs${SUFFIX}:diffs"; do 
    dir=${type%%:*}   # col 1: dir name
    pipe=${type##*:}  # col 2: pipe name
    
    # 1. Create hourly dir
    mkdir -p "$BOOK/$dir/hourly/$D"
    
    # 2. Link hourly file -> root pipe
    # e.g.: hl_book/node_fills_by_block/hourly/YYYYMMDD/H -> hl_book/fills
    ln -sf "$BOOK/$pipe" "$BOOK/$dir/hourly/$D/$H"
done

echo "✅ [book_tmpfs_init] Mode: $MODE | Target: UTC $D Hour $H"

