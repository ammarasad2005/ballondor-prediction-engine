"""Understat scraper for xG/xA data — alternative to Cloudflare-blocked fbref.

Per user request: 'for advanced metrics you could use alternatives that
dont have cloudflare restrictions such as understat api etc and make
the system even more robust'

Understat provides xG/xA data via a clean AJAX API that is NOT
Cloudflare-protected. Verified accessible in this sandbox.

Coverage:
  - Top-5 European leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1)
  - Seasons 2014-2024 (start year; e.g., "2023" = 2023-24 season)
  - Per-player season aggregates: xG, xA, goals, assists, shots,
    key_passes, yellow/red cards, position, team, games, minutes
  - Per-player per-match data also available (not used here)

Limitations:
  - No UCL/Europa League coverage (only domestic leagues)
  - No data before 2014-15 season (Understat's earliest coverage)
  - Per Architecture Blueprint P4, this aligns perfectly with the
    modern era definition (2014-15 onward)

API endpoint:
  GET https://understat.com/getLeagueData/{league}/{season}
  Headers:
    User-Agent: <browser UA>
    X-Requested-With: XMLHttpRequest  (required — without this, returns 404)
    Referer: https://understat.com/league/{league}/{season}
  Returns: JSON with 'teams', 'players', 'dates' keys.
  'players' is a list of dicts with xG/xA/etc per player for that season.

Idempotency:
  Each league/season response is cached to
  data/raw/understat/pages/{league}_{season}.json and skipped on rerun.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
CACHE_DIR = PROJECT_ROOT / "data" / "raw" / "understat" / "pages"
OUTPUT_JSONL = PROJECT_ROOT / "data" / "raw" / "understat" / "player_xg_raw.jsonl"
SCRAPE_LOG = PROJECT_ROOT / "data" / "raw" / "understat" / "scrape_log.md"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",  # CRITICAL — without this, returns 404
    "Referer": "https://understat.com/league/{league}/{season}",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_DELAY_S = 1.5
TIMEOUT_S = 30
MAX_RETRIES = 3

# League name mapping (Understat uses these slugs)
LEAGUES = {
    "EPL": "Premier League",
    "La_liga": "La Liga",
    "Serie_A": "Serie A",
    "Bundesliga": "Bundesliga",
    "Ligue_1": "Ligue 1",
}

# Seasons to fetch: 2014-2024 (start year, so 2014-15 through 2024-25)
SEASONS = list(range(2014, 2025))


# -----------------------------------------------------------------------------
# HTTP layer
# -----------------------------------------------------------------------------

def fetch_league_data(league: str, season: int) -> Optional[dict]:
    """Fetch league data from Understat. Returns parsed JSON or None on failure."""
    cache_path = CACHE_DIR / f"{league}_{season}.json"
    if cache_path.exists() and cache_path.stat().st_size > 5000:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    url = f"https://understat.com/getLeagueData/{league}/{season}"
    headers = HEADERS.copy()
    headers["Referer"] = f"https://understat.com/league/{league}/{season}"

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT_S)
            if r.status_code == 200 and len(r.content) > 5000:
                try:
                    data = r.json()
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    return data
                except json.JSONDecodeError as e:
                    print(f"    [{attempt}/{MAX_RETRIES}] JSON decode error for {league}/{season}: {e}")
                    last_err = e
            else:
                print(f"    [{attempt}/{MAX_RETRIES}] HTTP {r.status_code}, size={len(r.content)} for {league}/{season}")
                last_err = requests.exceptions.HTTPError(f"HTTP {r.status_code}")
            time.sleep(REQUEST_DELAY_S * attempt)
        except Exception as e:
            last_err = e
            print(f"    [{attempt}/{MAX_RETRIES}] {type(e).__name__} for {league}/{season}: {e}")
            time.sleep(REQUEST_DELAY_S * attempt)
    return None


# -----------------------------------------------------------------------------
# Player name normalization for matching
# -----------------------------------------------------------------------------

def normalize_name_for_match(s: str) -> str:
    """Aggressive normalization for matching against ground_truth player names.
    Lowercase, strip accents, collapse whitespace, strip punctuation.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape xG/xA data from Understat")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES.keys()),
                        help="League slugs to fetch (default: all 5)")
    parser.add_argument("--seasons", type=str, default="2014-2024",
                        help="Season range (default: 2014-2024)")
    args = parser.parse_args()

    # Parse season range
    if "-" in args.seasons:
        start, end = map(int, args.seasons.split("-"))
        seasons = list(range(start, end + 1))
    else:
        seasons = [int(args.seasons)]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"Scraping Understat: {len(args.leagues)} leagues × {len(seasons)} seasons")
    print(f"Leagues: {args.leagues}")
    print(f"Seasons: {seasons}")
    print("=" * 70)

    all_player_records = []
    fetch_summary = []

    for league_slug in args.leagues:
        league_name = LEAGUES.get(league_slug, league_slug)
        for season in seasons:
            print(f"\n  Fetching {league_slug}/{season} ({league_name})...")
            data = fetch_league_data(league_slug, season)
            if data is None:
                fetch_summary.append((league_slug, season, 0, "FAILED"))
                continue

            players = data.get("players", [])
            print(f"    Got {len(players)} players")

            for p in players:
                record = {
                    "understat_player_id": p.get("id"),
                    "player_name": p.get("player_name"),
                    "player_name_normalized": normalize_name_for_match(p.get("player_name", "")),
                    "league_slug": league_slug,
                    "league_name": league_name,
                    "season": str(season),
                    "season_label": f"{season}-{str(season + 1)[-2:]}",
                    "team_title": p.get("team_title"),
                    "games": int(p["games"]) if p.get("games") else None,
                    "time": int(p["time"]) if p.get("time") else None,  # minutes
                    "goals": int(p["goals"]) if p.get("goals") else None,
                    "assists": int(p["assists"]) if p.get("assists") else None,
                    "shots": int(p["shots"]) if p.get("shots") else None,
                    "key_passes": int(p["key_passes"]) if p.get("key_passes") else None,
                    "xG": float(p["xG"]) if p.get("xG") else None,
                    "xA": float(p["xA"]) if p.get("xA") else None,
                    "yellow_cards": int(p["yellow_cards"]) if p.get("yellow_cards") else None,
                    "red_cards": int(p["red_cards"]) if p.get("red_cards") else None,
                    "position": p.get("position"),
                    "npg": int(p["npg"]) if p.get("npg") else None,  # non-penalty goals
                    "npxG": float(p["npxG"]) if p.get("npxG") else None,  # non-penalty xG
                }
                all_player_records.append(record)

            fetch_summary.append((league_slug, season, len(players), "OK"))
            time.sleep(REQUEST_DELAY_S)

    # Write JSONL
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for r in all_player_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print()
    print("=" * 70)
    print("Fetch summary:")
    print("=" * 70)
    for league_slug, season, n_players, status in fetch_summary:
        print(f"  {league_slug:12} {season}: {status:8} ({n_players} players)")
    print(f"\nTotal player records: {len(all_player_records)}")
    print(f"Output: {OUTPUT_JSONL}")

    # Write scrape log
    log_lines = []
    log_lines.append("# Understat scrape log\n\n")
    log_lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n")
    log_lines.append(f"Total player records: {len(all_player_records)}\n\n")
    log_lines.append("| League | Season | Players | Status |\n|---|---|---|---|\n")
    for league_slug, season, n_players, status in fetch_summary:
        log_lines.append(f"| {league_slug} | {season} | {n_players} | {status} |\n")
    SCRAPE_LOG.write_text("".join(log_lines), encoding="utf-8")
    print(f"✅ Wrote {SCRAPE_LOG}")


if __name__ == "__main__":
    main()
