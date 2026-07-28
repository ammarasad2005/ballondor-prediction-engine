#!/bin/bash
# Pre-fetch all trophy/competition Wikipedia pages via the Action API using curl.
# (Same Python-rate-limit workaround as Phase 1/2.)
#
# Output: data/raw/trophies/pages_api/{category}_{year}.json
#
# Categories:
#   ucl_{year}.json                - UEFA Champions League / European Cup final
#   intl_world_cup_{year}.json     - FIFA World Cup
#   intl_euro_{year}.json          - UEFA European Championship
#   intl_copa_américa_{year}.json  - Copa América
#   league_{country}_{year}.json   - Top-5 European domestic leagues

set -e
PROJECT_ROOT="/home/z/my-project/ballondor-engine"
CACHE_DIR="$PROJECT_ROOT/data/raw/trophies/pages_api"
mkdir -p "$CACHE_DIR"

UA="BallonDorPredictBot/0.1 (research; contact: agent@local)"
DELAY=1.2
fetched=0; skipped=0; failed=0

fetch_page() {
    local PAGE_TITLE="$1"
    local OUT_FILE="$2"
    if [ -f "$OUT_FILE" ] && [ "$(wc -c < "$OUT_FILE")" -gt 1000 ]; then
        skipped=$((skipped + 1))
        return 0
    fi
    local ENCODED=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))" "$PAGE_TITLE")
    local URL="https://en.wikipedia.org/w/api.php?action=parse&prop=text&page=${ENCODED}&format=json&redirects=1"
    local success=false
    for attempt in 1 2 3; do
        local http_code=$(curl -sS -L --max-time 25 -A "$UA" -o "$OUT_FILE.tmp" -w "%{http_code}" "$URL" 2>/dev/null || echo "000")
        if [ "$http_code" = "200" ] && [ "$(wc -c < "$OUT_FILE.tmp")" -gt 1000 ]; then
            mv "$OUT_FILE.tmp" "$OUT_FILE"
            success=true
            fetched=$((fetched + 1))
            break
        fi
        rm -f "$OUT_FILE.tmp"
        sleep "$DELAY"
    done
    if ! $success; then
        failed=$((failed + 1))
        echo "    FAIL: $PAGE_TITLE"
    fi
    sleep "$DELAY"
}

echo "=== UEFA Champions League / European Cup (1955-2024) ==="
for year in $(seq 1955 2024); do
    yy=$((year + 1))
    yy2=$(printf "%02d" $((yy % 100)))
    if [ "$year" -lt 1992 ]; then
        page="${year}-${yy2} European Cup"
    else
        page="${year}-${yy2} UEFA Champions League"
    fi
    fetch_page "$page" "$CACHE_DIR/ucl_${year}.json"
    if [ $((fetched % 20)) -eq 0 ] && [ $fetched -gt 0 ]; then
        echo "  Progress: fetched=$fetched skipped=$skipped failed=$failed (last: UCL $year)"
    fi
done
echo "  UCL done: fetched=$fetched skipped=$skipped failed=$failed"

echo ""
echo "=== FIFA World Cup (1930-2022, excl 1942/1946) ==="
for year in 1930 1934 1938 1950 1954 1958 1962 1966 1970 1974 1978 1982 1986 1990 1994 1998 2002 2006 2010 2014 2018 2022; do
    fetch_page "${year} FIFA World Cup" "$CACHE_DIR/intl_world_cup_${year}.json"
done
echo "  World Cup done"

echo ""
echo "=== UEFA Euro (1960-2024, every 4 years) ==="
for year in 1960 1964 1968 1972 1976 1980 1984 1988 1992 1996 2000 2004 2008 2012 2016 2020 2024; do
    fetch_page "UEFA Euro ${year}" "$CACHE_DIR/intl_uefa_euro_${year}.json"
done
echo "  Euro done"

echo ""
echo "=== Copa América (1916-2024, irregular cadence) ==="
for year in 1916 1917 1919 1920 1921 1922 1923 1924 1925 1926 1927 1929 1935 1937 1939 1941 1942 1945 1946 1947 1949 1953 1955 1956 1957 1959 1963 1967 1975 1979 1983 1987 1989 1991 1993 1995 1997 1999 2001 2004 2007 2011 2015 2016 2019 2021 2024; do
    fetch_page "${year} Copa América" "$CACHE_DIR/intl_copa_américa_${year}.json"
done
echo "  Copa América done"

echo ""
echo "=== Top-5 European Leagues (1955-2024) ==="
for year in $(seq 1955 2024); do
    yy=$((year + 1))
    yy2=$(printf "%02d" $((yy % 100)))
    season="${year}-${yy2}"
    # England: Premier League (1992+) or First Division (before)
    if [ "$year" -ge 1992 ]; then
        fetch_page "${season} Premier League" "$CACHE_DIR/league_england_${year}.json"
    else
        fetch_page "${season} English First Division" "$CACHE_DIR/league_england_${year}.json"
    fi
    fetch_page "${season} La Liga" "$CACHE_DIR/league_spain_${year}.json"
    fetch_page "${season} Serie A" "$CACHE_DIR/league_italy_${year}.json"
    fetch_page "${season} Bundesliga" "$CACHE_DIR/league_germany_${year}.json"
    fetch_page "${season} Ligue 1" "$CACHE_DIR/league_france_${year}.json"
done
echo "  Leagues done"

echo ""
echo "=============================================="
echo "Trophy fetch complete:"
echo "  fetched=$fetched  skipped=$skipped  failed=$failed"
echo "  Total cached files: $(ls "$CACHE_DIR"/*.json 2>/dev/null | wc -l)"
