#!/bin/bash
# Pre-fetch all player Wikipedia pages via the Action API using curl.
# Python's requests/urllib is rate-limited (per-IP) by Wikipedia in this
# sandbox, but curl uses a different egress IP that works.
#
# Input:  data/raw/stats/_player_list.txt (tab-separated: name<TAB>slug)
# Output: data/raw/stats/pages_api/{slug}.json (one file per player)
#
# Idempotent: skips slugs whose cache file already exists with non-trivial size.
#
# Usage:
#   ./scrapers/_fetch_player_pages_curl.sh [start_line] [end_line]
#   (line numbers are 1-indexed into _player_list.txt; default: all lines)

set -e
PROJECT_ROOT="/home/z/my-project/ballondor-engine"
INPUT="$PROJECT_ROOT/data/raw/stats/_player_list.txt"
CACHE_DIR="$PROJECT_ROOT/data/raw/stats/pages_api"
LOG_FILE="$PROJECT_ROOT/data/raw/stats/_fetch_log.txt"

mkdir -p "$CACHE_DIR"

if [ ! -f "$INPUT" ]; then
    echo "Error: $INPUT not found. Run scrapers/_export_player_list.py first."
    exit 1
fi

TOTAL_LINES=$(wc -l < "$INPUT")
START_LINE=${1:-1}
END_LINE=${2:-$TOTAL_LINES}

echo "Fetching lines $START_LINE-$END_LINE of $TOTAL_LINES from $INPUT"
echo "Cache dir: $CACHE_DIR"
echo ""

UA="BallonDorPredictBot/0.1 (research; contact: agent@local)"
DELAY=1.2
fetched=0; skipped=0; failed=0; missing=0

# Read line by line, supporting start/end range
LINE_NUM=0
while IFS=$'\t' read -r NAME SLUG; do
    LINE_NUM=$((LINE_NUM + 1))
    if [ "$LINE_NUM" -lt "$START_LINE" ]; then continue; fi
    if [ "$LINE_NUM" -gt "$END_LINE" ]; then break; fi

    # Skip empty lines
    [ -z "$SLUG" ] && continue

    out="$CACHE_DIR/${SLUG}.json"

    # Skip if already cached with reasonable size
    if [ -f "$out" ] && [ "$(wc -c < "$out")" -gt 1000 ]; then
        skipped=$((skipped + 1))
        continue
    fi

    # URL-encode the slug for the API call (Wikipedia API expects %XX for non-ASCII)
    ENCODED_SLUG=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))" "$SLUG")
    url="https://en.wikipedia.org/w/api.php?action=parse&prop=text&page=${ENCODED_SLUG}&format=json&redirects=1"

    success=false
    http_code="000"
    for attempt in 1 2 3; do
        http_code=$(curl -sS -L --max-time 25 -A "$UA" -o "$out.tmp" -w "%{http_code}" "$url" 2>/dev/null || echo "000")
        if [ "$http_code" = "200" ] && [ "$(wc -c < "$out.tmp")" -gt 1000 ]; then
            # Check if API returned an error (missing page)
            if grep -q '"error"' "$out.tmp" 2>/dev/null; then
                # API error — page doesn't exist
                mv "$out.tmp" "$out"
                success=true
                missing=$((missing + 1))
                printf "  [%4d] MISS %s (page missing)\n" "$LINE_NUM" "$NAME"
                break
            fi
            mv "$out.tmp" "$out"
            success=true
            fetched=$((fetched + 1))
            if [ $((fetched % 25)) -eq 0 ]; then
                printf "  [%4d] OK   %s (%d bytes) | fetched=%d skipped=%d missing=%d failed=%d\n" \
                    "$LINE_NUM" "$NAME" "$(wc -c < "$out")" "$fetched" "$skipped" "$missing" "$failed"
            fi
            break
        fi
        rm -f "$out.tmp"
        sleep "$DELAY"
    done

    if ! $success; then
        failed=$((failed + 1))
        echo "  [$LINE_NUM] FAIL $NAME (HTTP $http_code after 3 tries)" | tee -a "$LOG_FILE"
    fi

    sleep "$DELAY"
done < "$INPUT"

echo ""
echo "=============================================="
echo "Fetch batch lines $START_LINE-$END_LINE complete:"
echo "  fetched=$fetched  skipped=$skipped  missing(page)=$missing  failed=$failed"
echo "  Total cached files: $(ls "$CACHE_DIR"/*.json 2>/dev/null | wc -l)"
