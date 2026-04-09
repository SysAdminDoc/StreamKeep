#!/bin/bash
# Fishtank Kick Stream Downloader
# Downloads ~8h48m VOD in 1-hour increments at 1080p
# Stream: 2026-04-08 18:48 UTC to 2026-04-09 03:37 UTC

PLAYLIST="https://stream.kick.com/3c81249a5ce0/ivs/v1/196233775518/BbX2MLgQMEcl/2026/4/8/18/48/sIh1BWhNc4xl/media/hls/1080p/playlist.m3u8"
OUTDIR="$HOME/Desktop/fishtank"
mkdir -p "$OUTDIR"

# Total duration: 31731 seconds (~8h48m31s)
# 8 full hours + 1 partial (48m31s)
HOUR=3600

echo "=== Fishtank VOD Downloader ==="
echo "Output: $OUTDIR"
echo "Quality: 1080p (best)"
echo "Total: ~8h 48m in 1-hour segments"
echo ""

for i in $(seq 1 9); do
    START=$(( (i - 1) * HOUR ))

    # Last segment gets remaining time
    if [ $i -eq 9 ]; then
        DURATION=2931  # 48m31s remainder
    else
        DURATION=$HOUR
    fi

    OUTFILE="$OUTDIR/fishtank_hour${i}.mp4"

    if [ -f "$OUTFILE" ]; then
        echo "[SKIP] Hour $i already exists: $OUTFILE"
        continue
    fi

    echo "[${i}/9] Downloading hour $i (start: ${START}s, duration: ${DURATION}s)..."
    ffmpeg -hide_banner -loglevel warning -stats \
        -ss "$START" \
        -i "$PLAYLIST" \
        -t "$DURATION" \
        -c copy \
        "$OUTFILE"

    if [ $? -eq 0 ]; then
        SIZE=$(du -h "$OUTFILE" | cut -f1)
        echo "[DONE] Hour $i complete ($SIZE)"
    else
        echo "[FAIL] Hour $i failed! Retrying..."
        ffmpeg -hide_banner -loglevel warning -stats \
            -ss "$START" \
            -i "$PLAYLIST" \
            -t "$DURATION" \
            -c copy \
            "$OUTFILE"
    fi
    echo ""
done

echo "=== All segments downloaded to $OUTDIR ==="
ls -lh "$OUTDIR"
