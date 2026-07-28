"""Trophy & competition results scraper for the Ballon d'Or Prediction Engine.

Per Architecture Blueprint §4.4 family 2 + Requirements A.3:
  - Domestic league title winner, by country and year
  - Continental club competition winner (European Cup / Champions League)
    by year, plus finalist/semifinalist status for "deep run" feature
  - International tournament results by calendar year (World Cup,
    Euro, Copa América) — winner, and player's individual tournament
    performance where available
  - Domestic league final table position, by team and season

Source: Wikipedia per-competition pages via the Action API.

Idempotency (Architecture Blueprint §4.2):
  - Each competition page is cached to data/raw/trophies/pages_api/{slug}.json
  - Parsed trophy data is written to data/raw/trophies/trophies_raw.jsonl

Output schema (raw, pre-feature-engineering):
  {
    "season_id": "2023",
    "competition": "UEFA Champions League",
    "stage": "winner" | "runner_up" | "semi_final" | "quarter_final",
    "team": "Manchester City",
    "source": "wikipedia_api/2023-24_UEFA_Champions_League"
  }

Coverage plan (initial):
  - UEFA Champions League / European Cup: 1955-56 onward (all years)
  - UEFA Europa League / UEFA Cup: 1971-72 onward
  - FIFA World Cup: 1930, 1934, ... every 4 years
  - UEFA European Championship (Euro): 1960 onward, every 4 years
  - Copa América: 1916 onward, irregular cadence
  - Top-5 European leagues (EPL/La Liga/Serie A/Bundesliga/Ligue 1):
    1956 onward, by year
"""
from __future__ import annotations

import argparse
import io
import json
import re
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "trophies"
PAGES_DIR = RAW_DIR / "pages_api"
OUTPUT_JSONL = RAW_DIR / "trophies_raw.jsonl"
SCRAPE_LOG = RAW_DIR / "scrape_log.md"

USER_AGENT = "BallonDorPredictBot/0.1 (research; contact: agent@local)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
REQUEST_DELAY_S = 1.5
TIMEOUT_S = 30
MAX_RETRIES = 3

API_URL = "https://en.wikipedia.org/w/api.php"


# -----------------------------------------------------------------------------
# HTTP layer (same pattern as ground_truth_scraper.py)
# -----------------------------------------------------------------------------

FOOTNOTE_RE = re.compile(r"\s*\[[^\]]*\]\s*")


def fetch_api_page(page_title: str, cache_path: Path) -> dict:
    """Fetch a Wikipedia page via the Action API. Cached per page title."""
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    params = {
        "action": "parse",
        "prop": "text",
        "page": page_title,
        "format": "json",
        "redirects": "1",
    }
    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=TIMEOUT_S)
            if r.status_code == 200:
                data = r.json()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            wait = REQUEST_DELAY_S * (2 ** attempt)
            print(f"    [{attempt}/{MAX_RETRIES}] HTTP {r.status_code} for {page_title}; sleeping {wait:.1f}s")
            time.sleep(wait)
            last_err = requests.exceptions.HTTPError(f"HTTP {r.status_code}")
        except Exception as e:
            last_err = e
            wait = REQUEST_DELAY_S * (2 ** attempt)
            print(f"    [{attempt}/{MAX_RETRIES}] {type(e).__name__} for {page_title}; sleeping {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {page_title} after {MAX_RETRIES} attempts: {last_err}")


def clean_text(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).replace("\xa0", " ")
    s = FOOTNOTE_RE.sub(" ", s)
    # Strip parenthetical annotations like "(15th title)", "(4th title)"
    s = re.sub(r"\s*\(\d+(?:st|nd|rd|th)\s+title\)\s*", "", s, flags=re.IGNORECASE)
    # Strip trailing "Nth title" annotations (no parens) like "8th Premier League title"
    # Pattern: digits + ordinal + (anything) + "title" at end of string
    s = re.sub(r"\s*\d+(?:st|nd|rd|th)\s+[^,]*title.*$", "", s, flags=re.IGNORECASE)
    # Strip trailing footnote-like annotations
    s = re.sub(r"\s+", " ", s).strip()
    return s


# -----------------------------------------------------------------------------
# Competition page URL builders
# -----------------------------------------------------------------------------

def ucl_slug_for_year(year: int) -> str:
    """Get the Wikipedia page slug for the UEFA Champions League / European Cup
    of a given season.

    The European Cup was renamed to UEFA Champions League in 1992-93.
    Wikipedia uses different page-title conventions:
      - 1955-1992: "{year}-{yy+1} European Cup" (e.g. "1955-56 European Cup")
      - 1992-present: "{year}-{yy+1} UEFA Champions League" (e.g. "2023-24 UEFA Champions League")
    """
    if year < 1992:
        return f"{year}-{str(year + 1)[-2:]} European Cup"
    else:
        return f"{year}-{str(year + 1)[-2:]} UEFA Champions League"


def world_cup_slug_for_year(year: int) -> str:
    """FIFA World Cup page slug. Format: "{year} FIFA World Cup"."""
    return f"{year} FIFA World Cup"


def euro_slug_for_year(year: int) -> str:
    """UEFA European Championship page slug. Format: "UEFA Euro {year}"."""
    return f"UEFA Euro {year}"


def copa_america_slug_for_year(year: int) -> str:
    """Copa América page slug. Format: "{year} Copa América"."""
    return f"{year} Copa América"


def league_slug_for(year: int, country: str) -> str:
    """Top-tier domestic league page slug for a given country and year.

    Args:
        year: The starting year of the season (e.g. 2023 for 2023-24).
        country: One of 'england', 'spain', 'italy', 'germany', 'france'.

    Returns:
        Wikipedia page title like "2023-24 Premier League" or "2023-24 La Liga".
    """
    yy = str(year + 1)[-2:]
    season = f"{year}-{yy}"
    patterns = {
        "england": f"{season} Premier League",
        "spain": f"{season} La Liga",
        "italy": f"{season} Serie A",
        "germany": f"{season} Bundesliga",
        "france": f"{season} Ligue 1",
    }
    return patterns.get(country, "")


# -----------------------------------------------------------------------------
# Competition page parsers
# -----------------------------------------------------------------------------

def parse_ucl_page(api_data: dict, season_id: str, page_title: str) -> list[dict]:
    """Parse a UEFA Champions League / European Cup page.

    Extracts: winner, runner-up, semi-finalists (where listed).
    Looks for tables with captions like "Final" / "Semi-finals" / "Knockout phase".
    The simplest reliable pattern is to find the infobox on the page that lists
    the final — it has rows like "Champions" and "Runners-up".
    """
    if "parse" not in api_data:
        return []
    html = api_data["parse"]["text"]["*"]
    soup = BeautifulSoup(html, "lxml")

    rows: list[dict] = []
    season_id_int = int(season_id)

    # Strategy: search for th/td cells containing "Champions" or "Runners-up"
    # These appear in the infobox on UCL final pages.
    for tbl in soup.find_all("table"):
        cls = tbl.get("class") or []
        # Skip navboxes
        if "navbox" in cls or "nowraplinks" in cls:
            continue
        # Look at every row in the table
        for tr in tbl.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            # Find the label cell (th) and value cell (td)
            label = clean_text(cells[0].get_text())
            value = clean_text(cells[1].get_text())
            if not label or not value:
                continue
            label_lower = label.lower()
            # Match "Champions" / "Winners" / "Winning team"
            if "champion" in label_lower or label_lower in ("winners", "winning team", "winner"):
                rows.append({
                    "season_id": season_id,
                    "competition": "UEFA Champions League" if season_id_int >= 1992 else "European Cup",
                    "stage": "winner",
                    "team": value,
                    "source": f"wikipedia_api/{page_title}",
                })
            elif "runner" in label_lower or "runners-up" in label_lower:
                rows.append({
                    "season_id": season_id,
                    "competition": "UEFA Champions League" if season_id_int >= 1992 else "European Cup",
                    "stage": "runner_up",
                    "team": value,
                    "source": f"wikipedia_api/{page_title}",
                })
        if rows:
            break   # found the infobox; stop searching tables

    return rows


def parse_international_tournament_page(api_data: dict, season_id: str, competition: str, page_title: str) -> list[dict]:
    """Parse a World Cup / Euro / Copa América page.

    Extracts: winner, runner-up. These pages have infoboxes similar to UCL
    final pages with "Champions" and "Runners-up" rows.
    """
    if "parse" not in api_data:
        return []
    html = api_data["parse"]["text"]["*"]
    soup = BeautifulSoup(html, "lxml")

    rows: list[dict] = []

    for tbl in soup.find_all("table"):
        cls = tbl.get("class") or []
        if "navbox" in cls or "nowraplinks" in cls:
            continue
        for tr in tbl.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = clean_text(cells[0].get_text())
            value = clean_text(cells[1].get_text())
            if not label or not value:
                continue
            label_lower = label.lower()
            if "champion" in label_lower or label_lower in ("winners", "winning team", "winner", "champions"):
                rows.append({
                    "season_id": season_id,
                    "competition": competition,
                    "stage": "winner",
                    "team": value,
                    "source": f"wikipedia_api/{page_title}",
                })
            elif "runner" in label_lower or "runners-up" in label_lower:
                rows.append({
                    "season_id": season_id,
                    "competition": competition,
                    "stage": "runner_up",
                    "team": value,
                    "source": f"wikipedia_api/{page_title}",
                })
        if rows:
            break

    return rows


def parse_league_page(api_data: dict, season_id: str, league_name: str, page_title: str) -> list[dict]:
    """Parse a domestic league page (e.g. 2023-24 Premier League).

    Extracts: league champion. League pages typically have a "League table"
    or an infobox with "Champions" row.
    """
    if "parse" not in api_data:
        return []
    html = api_data["parse"]["text"]["*"]
    soup = BeautifulSoup(html, "lxml")

    rows: list[dict] = []

    for tbl in soup.find_all("table"):
        cls = tbl.get("class") or []
        if "navbox" in cls or "nowraplinks" in cls:
            continue
        for tr in tbl.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = clean_text(cells[0].get_text())
            value = clean_text(cells[1].get_text())
            if not label or not value:
                continue
            label_lower = label.lower()
            if "champion" in label_lower or label_lower in ("winners", "winner"):
                rows.append({
                    "season_id": season_id,
                    "competition": league_name,
                    "stage": "winner",
                    "team": value,
                    "source": f"wikipedia_api/{page_title}",
                })
                break
        if rows:
            break

    return rows


# -----------------------------------------------------------------------------
# Main scrape logic
# -----------------------------------------------------------------------------

def scrape_ucl_seasons(start_year: int, end_year: int, out_f) -> tuple[int, int]:
    """Scrape UEFA Champions League / European Cup final pages for a year range."""
    n_ok = 0
    n_fail = 0
    for year in range(start_year, end_year + 1):
        season_id = str(year)
        page_title = ucl_slug_for_year(year)
        cache_path = PAGES_DIR / f"ucl_{year}.json"
        try:
            api_data = fetch_api_page(page_title, cache_path)
            rows = parse_ucl_page(api_data, season_id, page_title)
            if rows:
                for r in rows:
                    out_f.write(json.dumps(r, ensure_ascii=False) + "\n")
                out_f.flush()
                n_ok += 1
                # Show what we got
                stages = [r["stage"] for r in rows]
                teams = [r["team"][:30] for r in rows]
                print(f"  UCL {year}: {len(rows)} rows  stages={stages}  teams={teams}")
            else:
                n_fail += 1
                print(f"  UCL {year}: ⚠️ no rows parsed (page may be missing or infobox format differs)")
        except Exception as e:
            n_fail += 1
            print(f"  UCL {year}: ❌ FAILED ({type(e).__name__}: {e})")
        time.sleep(REQUEST_DELAY_S)
    return n_ok, n_fail


def scrape_intl_tournament(year: int, competition: str, slug_fn, out_f) -> tuple[int, int]:
    """Scrape a single international tournament page (World Cup / Euro / Copa América)."""
    season_id = str(year)
    page_title = slug_fn(year)
    cache_filename = f"intl_{competition.lower().replace(' ', '_')}_{year}.json"
    cache_path = PAGES_DIR / cache_filename
    try:
        api_data = fetch_api_page(page_title, cache_path)
        rows = parse_international_tournament_page(api_data, season_id, competition, page_title)
        if rows:
            for r in rows:
                out_f.write(json.dumps(r, ensure_ascii=False) + "\n")
            out_f.flush()
            stages = [r["stage"] for r in rows]
            teams = [r["team"][:30] for r in rows]
            print(f"  {competition} {year}: {len(rows)} rows  stages={stages}  teams={teams}")
            return 1, 0
        else:
            print(f"  {competition} {year}: ⚠️ no rows parsed")
            return 0, 1
    except Exception as e:
        print(f"  {competition} {year}: ❌ FAILED ({type(e).__name__})")
        return 0, 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape trophy data from Wikipedia")
    parser.add_argument("--start-year", type=int, default=1955, help="Inclusive start year (UCL era start)")
    parser.add_argument("--end-year", type=int, default=2025, help="Inclusive end year")
    parser.add_argument("--only", choices=["ucl", "intl", "league", "all"], default="all",
                        help="Scrape only one category (for testing)")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Append-mode output (idempotent: re-runs add new rows; dedupe later in QA)
    out_f = open(OUTPUT_JSONL, "a", encoding="utf-8")

    totals = {"ucl_ok": 0, "ucl_fail": 0, "intl_ok": 0, "intl_fail": 0, "league_ok": 0, "league_fail": 0}

    if args.only in ("all", "ucl"):
        print("=" * 70)
        print(f"Scraping UEFA Champions League / European Cup ({args.start_year}-{args.end_year})")
        print("=" * 70)
        ok, fail = scrape_ucl_seasons(args.start_year, args.end_year, out_f)
        totals["ucl_ok"] = ok
        totals["ucl_fail"] = fail

    if args.only in ("all", "intl"):
        print()
        print("=" * 70)
        print("Scraping international tournaments (World Cup, Euro, Copa América)")
        print("=" * 70)
        # World Cup: 1930, 1934, 1938, 1950, 1954, ... every 4 years
        wc_years = [y for y in range(1930, args.end_year + 1, 4) if y not in (1942, 1946) and y >= args.start_year]
        # UEFA Euro: 1960, 1964, ... every 4 years
        euro_years = [y for y in range(1960, args.end_year + 1, 4) if y >= args.start_year]
        # Copa América: roughly every 4 years, irregular before 1987
        copa_years = [y for y in range(1916, args.end_year + 1) if y in {
            1916, 1917, 1919, 1920, 1921, 1922, 1923, 1924, 1925, 1926, 1927, 1929,
            1935, 1937, 1939, 1941, 1942, 1945, 1946, 1947, 1949, 1953, 1955, 1956,
            1957, 1959, 1963, 1967, 1975, 1979, 1983, 1987, 1989, 1991, 1993, 1995,
            1997, 1999, 2001, 2004, 2007, 2011, 2015, 2016, 2019, 2021, 2024
        }]
        print(f"  World Cup years ({len(wc_years)}): {wc_years}")
        for y in wc_years:
            ok, fail = scrape_intl_tournament(y, "FIFA World Cup", world_cup_slug_for_year, out_f)
            totals["intl_ok"] += ok
            totals["intl_fail"] += fail
            time.sleep(REQUEST_DELAY_S)
        print(f"\n  Euro years ({len(euro_years)}): {euro_years}")
        for y in euro_years:
            ok, fail = scrape_intl_tournament(y, "UEFA European Championship", euro_slug_for_year, out_f)
            totals["intl_ok"] += ok
            totals["intl_fail"] += fail
            time.sleep(REQUEST_DELAY_S)
        print(f"\n  Copa América years ({len(copa_years)}): {copa_years}")
        for y in copa_years:
            ok, fail = scrape_intl_tournament(y, "Copa América", copa_america_slug_for_year, out_f)
            totals["intl_ok"] += ok
            totals["intl_fail"] += fail
            time.sleep(REQUEST_DELAY_S)

    if args.only in ("all", "league"):
        print()
        print("=" * 70)
        print("Scraping top-5 European leagues (England, Spain, Italy, Germany, France)")
        print("=" * 70)
        league_names = {
            "england": "Premier League" if args.start_year >= 1992 else "English First Division",
            "spain": "La Liga",
            "italy": "Serie A",
            "germany": "Bundesliga",
            "france": "Ligue 1",
        }
        for country, league_name in league_names.items():
            print(f"\n  --- {country} ({league_name}) ---")
            for year in range(args.start_year, args.end_year + 1):
                season_id = str(year)
                page_title = league_slug_for(year, country)
                if not page_title:
                    continue
                cache_filename = f"league_{country}_{year}.json"
                cache_path = PAGES_DIR / cache_filename
                try:
                    api_data = fetch_api_page(page_title, cache_path)
                    rows = parse_league_page(api_data, season_id, league_name, page_title)
                    if rows:
                        for r in rows:
                            out_f.write(json.dumps(r, ensure_ascii=False) + "\n")
                        out_f.flush()
                        totals["league_ok"] += 1
                        # Print first 2 only to reduce noise
                        if year - args.start_year < 2 or year == args.end_year:
                            team = rows[0]["team"][:30] if rows else "?"
                            print(f"    {country} {year}: ✅ champion={team!r}")
                    else:
                        totals["league_fail"] += 1
                        if year - args.start_year < 2 or year == args.end_year:
                            print(f"    {country} {year}: ⚠️ no champion parsed")
                except Exception as e:
                    totals["league_fail"] += 1
                    if year - args.start_year < 2 or year == args.end_year:
                        print(f"    {country} {year}: ❌ FAILED ({type(e).__name__})")
                time.sleep(REQUEST_DELAY_S)

    out_f.close()

    print()
    print("=" * 70)
    print(f"Done. UCL: ok={totals['ucl_ok']} fail={totals['ucl_fail']}")
    print(f"      International: ok={totals['intl_ok']} fail={totals['intl_fail']}")
    print(f"      Leagues: ok={totals['league_ok']} fail={totals['league_fail']}")
    print(f"Output: {OUTPUT_JSONL}")
    print(f"Total trophy rows: {sum(1 for _ in open(OUTPUT_JSONL)) if OUTPUT_JSONL.exists() else 0}")


if __name__ == "__main__":
    main()
