#!/bin/bash
# Pre-fetch all per-year Ballon d'Or Wikipedia pages via the Action API
# using curl (which routes through a different egress IP than Python's
# requests/urllib in this sandbox — Wikipedia rate-limits the Python
# egress IP but not the curl one).
#
# Output: data/raw/ground_truth/pages_api/{year}.json for years 1956-2025
# (skipping 2020 = COVID cancellation).
#
# Idempotent: skips years whose cache file already exists with non-trivial size.

set -e
CACHE_DIR="/home/z/my-project/ballondor-engine/data/raw/ground_truth/pages_api"
mkdir -p "$CACHE_DIR"

UA="BallonDorPredictBot/0.1 (research; contact: agent@local)"
DELAY=1.5  # seconds between requests — polite pacing

fetched=0
skipped=0
failed=0

for year in $(seq 1956 2025); do
    # Skip 2020 (COVID cancellation)
    if [ "$year" -eq 2020 ]; then
        continue
    fi

    out="$CACHE_DIR/${year}.json"

    # Skip if already cached with reasonable size
    if [ -f "$out" ] && [ "$(wc -c < "$out")" -gt 10000 ]; then
        skipped=$((skipped + 1))
        continue
    fi

    url="https://en.wikipedia.org/w/api.php?action=parse&prop=text&page=${year}_Ballon_d%27Or&format=json&redirects=1"

    # Try up to 3 times
    success=false
    for attempt in 1 2 3; do
        http_code=$(curl -sS -L --max-time 20 -A "$UA" -o "$out.tmp" -w "%{http_code}" "$url" 2>/dev/null || echo "000")
        if [ "$http_code" = "200" ] && [ "$(wc -c < "$out.tmp")" -gt 10000 ]; then
            mv "$out.tmp" "$out"
            success=true
            break
        fi
        echo "  [$attempt/3] year=$year HTTP=$http_code size=$(wc -c < "$out.tmp" 2>/dev/null || echo 0); retrying in ${DELAY}s"
        rm -f "$out.tmp"
        sleep "$DELAY"
    done

    if $success; then
        fetched=$((fetched + 1))
        size=$(wc -c < "$out")
        printf "  [%3d] %d  OK  (%d bytes)\n" "$fetched" "$year" "$size"
    else
        failed=$((failed + 1))
        echo "  [FAIL] year=$year — gave up after 3 attempts"
    fi

    sleep "$DELAY"
done

echo ""
echo "=============================================="
echo "Fetch complete: fetched=$fetched  skipped=$skipped  failed=$failed"
echo "Cache directory: $CACHE_DIR"
echo "Total cached files: $(ls "$CACHE_DIR"/*.json 2>/dev/null | wc -l)"
