"""Ground-truth backbone scraper for the Ballon d'Or Prediction Engine.

Per Architecture Blueprint §4.1, ground_truth.parquet is the single
load-bearing table — every downstream join anchors to it. Per Key Focus
Areas §1, errors here propagate silently everywhere, so this scraper
prioritizes transparency over smoothness: any row that doesn't parse
cleanly is logged and surfaced, never silently dropped.

Coverage strategy:
  - All years 1956-2025 (skipping 2020 = COVID cancellation, recorded
    as an explicit gap row per Implementation Plan Phase 1 task 1).
  - Source: Wikipedia Action API
    (https://en.wikipedia.org/w/api.php?action=parse&prop=text&page=...).
    This is preferred over direct HTML page fetches because:
      (a) it returns just the article body HTML (406 KB vs 710 KB),
          reducing server load and our parse time;
      (b) the API endpoint is designed for programmatic access and has
          not been rate-limiting us, whereas direct HTML fetches to
          en.wikipedia.org/wiki/* were returning HTTP 403 "Too Many
          Reqs" within ~5 requests;
      (c) it's the canonical machine-readable interface, which is what
          a polite bot should be using.
  - Each per-year page has ~25-30 nominees (fuller than the main
    "Ballon d'Or" page's compact top-3-per-year historical table).
    Uniform coverage across eras avoids the classical/modern nominee-
    list asymmetry that would otherwise bias the pairwise ranking
    training set (Key Focus Areas §4 — survivorship bias).

Idempotency (Architecture Blueprint §4.2):
  Every per-year API response is cached to
  data/raw/ground_truth/pages_api/{year}.json and skipped on rerun.
  The combined JSONL at data/raw/ground_truth/nominees_raw.jsonl is
  rewritten atomically on each successful run.

Output schema (raw, pre-entity-resolution — canonical name comes in
Phase 3):
  {
    "season_id": "2023",
    "award_year": 2023,
    "rank": 1,
    "player_name_raw": "Lionel Messi",
    "club_at_time": "Inter Miami",
    "nation_team": "Argentina",
    "points": 462.0,
    "source": "wikipedia_api/2023_Ballon_d%27Or"
  }
"""
from __future__ import annotations

import argparse
import io
import json
import os
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
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "ground_truth"
PAGES_DIR = RAW_DIR / "pages_api"   # Action API JSON responses
OUTPUT_JSONL = RAW_DIR / "nominees_raw.jsonl"
SCRAPE_LOG = RAW_DIR / "scrape_log.md"

USER_AGENT = "BallonDorPredictBot/0.1 (research; contact: agent@local)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
REQUEST_DELAY_S = 1.8   # polite pacing per Requirements B.2
TIMEOUT_S = 30
MAX_RETRIES = 3

API_URL = "https://en.wikipedia.org/w/api.php"

# Years to scrape: 1956-2025, skip 2020 (COVID cancellation).
SCRAPE_YEARS = [y for y in range(1956, 2026) if y != 2020]
# Explicitly cancelled years (gap, not error).
CANCELLED_YEARS = {2020: "Cancelled due to COVID-19 pandemic"}


# -----------------------------------------------------------------------------
# HTTP layer
# -----------------------------------------------------------------------------


def fetch_api_page(year: int, cache_path: Path) -> dict:
    """Fetch a per-year Wikipedia page via the Action API. Cached per-year."""
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    params = {
        "action": "parse",
        "prop": "text",
        "page": f"{year}_Ballon_d'Or",
        "format": "json",
        "redirects": "1",
    }
    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=TIMEOUT_S)
            if r.status_code == 200:
                data = r.json()
                if "parse" in data:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    return data
                # API returned an error payload (e.g., missing page)
                err = data.get("error", {}).get("info", "unknown API error")
                print(f"    API error for {year}: {err}")
                return data  # return for logging; parser will handle missing 'parse'
            if r.status_code in (429, 503):
                wait = REQUEST_DELAY_S * (2 ** attempt) * 2
                print(f"    [{attempt}/{MAX_RETRIES}] HTTP {r.status_code} for {year}; sleeping {wait:.1f}s")
                time.sleep(wait)
                continue
            # Other 4xx/5xx (e.g., 403 rate limit) — log + retry with backoff
            wait = REQUEST_DELAY_S * (2 ** attempt)
            print(f"    [{attempt}/{MAX_RETRIES}] HTTP {r.status_code} for {year}; sleeping {wait:.1f}s")
            time.sleep(wait)
            last_err = requests.exceptions.HTTPError(f"HTTP {r.status_code}: {r.text[:200]}")
            continue
        except Exception as e:
            last_err = e
            wait = REQUEST_DELAY_S * (2 ** attempt)
            print(f"    [{attempt}/{MAX_RETRIES}] {type(e).__name__} for {year}; sleeping {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {year} after {MAX_RETRIES} attempts: {last_err}")


# -----------------------------------------------------------------------------
# Cleaning helpers
# -----------------------------------------------------------------------------

FOOTNOTE_RE = re.compile(r"\s*\[[^\]]*\]\s*")


def clean_text(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).replace("\xa0", " ")
    s = FOOTNOTE_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_rank(v) -> Optional[int]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().lower()
    s = re.sub(r"(st|nd|rd|th)$", "", s)
    s = re.sub(r"\.0+$", "", s)
    if s in ("", "nan", "none", "-", "—"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_points(v) -> Optional[float]:
    """Parse points/percent values.

    Handles:
      - Integers/floats: 47, 144.0
      - Comma-separated: 1,170
      - Percentages (FIFA merger era): "22.65%" -> 22.65
      - Empty/NaN/None/dashes -> None
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace(",", "")
    # Strip trailing "%" (FIFA merger era 2010-2015 uses Percent column)
    s = s.rstrip("%").strip()
    if s.lower() in ("", "nan", "none", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def get_first_col(df: pd.DataFrame, col: str):
    """Return the first column matching `col` name as a pandas Series.

    Handles duplicate column names (e.g., 2003-2006 pages have two
    'points' columns because 'Total' and 'Votes' both map to 'points').
    Returns None if no such column exists.
    """
    if col not in df.columns:
        return None
    # If duplicate columns exist, df[col] returns a DataFrame; take first column
    s = df[col]
    if isinstance(s, pd.DataFrame):
        return s.iloc[:, 0]
    return s


# -----------------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------------


def normalize_columns(cols: list[str]) -> list[str]:
    """Normalize column names to canonical: rank, player, nationality, position,
    club, points, total_votes.

    Wikipedia per-year pages have variant column names:
      - 'Rank' (rare: 'Pos' or 'Position' is the player's FIELD position
        like GK/DF/MF/FW, NOT the ranking — must NOT be aliased to 'rank')
      - 'Player' or 'Name' (older pages)
      - 'Nationality' or 'Nation' or 'National team'
      - 'Club(s)' or 'Club' or 'Team' or 'Clubs'
      - 'Points' or 'Pts' or 'Total' or 'Percent' (the score column)
      - 'Votes' (separate from points — total number of votes received,
        distinct from points which are weighted by vote position)

    Also strips Wikipedia footnote markers from header text, e.g.
    'Rank[2][3]' -> 'Rank' -> 'rank'.
    """
    out = []
    for c in cols:
        c = str(c).strip().lower()
        # Strip footnote markers like "[2]", "[3]", "[note 1]"
        c = re.sub(r"\[[^\]]*\]", "", c)
        # Remove parenthetical clarifications like "club(s)" -> "club"
        c = re.sub(r"\(s\)", "", c)
        c = c.strip()
        if c == "rank":
            out.append("rank")
        elif c in ("pos", "position"):
            # Player's field position (GK/DF/MF/FW) — NOT the ranking.
            out.append("position")
        elif c in ("player", "name"):
            out.append("player")
        elif c in ("nationality", "nation", "country", "national team"):
            out.append("nationality")
        elif c in ("club", "team", "clubs"):
            out.append("club")
        elif c in ("points", "pts", "total", "percent"):
            # 'Total' (2003-2006) and 'Percent' (2010-2015 FIFA era) are
            # the score-like columns. Map to 'points' but note the unit
            # varies by era — this is documented in the QA report.
            out.append("points")
        elif c in ("votes", "total votes"):
            # Number of votes received — distinct from points. Keep separate
            # so we don't collide with the points column.
            out.append("total_votes")
        else:
            out.append(c)
    return out


def parse_per_year_api(api_data: dict, year: int) -> tuple[list[dict], str]:
    """Parse a per-year Wikipedia Action API response.

    Returns (rows, status_message). On failure, returns ([], "reason").

    Special handling for the FIFA Ballon d'Or merger era (2010-2015):
    these per-year pages split the men's ranking across TWO tables —
    table[0] has the top 3 (winner + 2 runners-up), table[1] has ranks 4+.
    Both have identical column structure. We detect this case by checking
    if the first ranking table has only ~3 data rows AND a second table
    with the same column headers exists immediately after, and if so
    concatenate them.
    """
    if "parse" not in api_data:
        err = api_data.get("error", {}).get("info", "missing 'parse' key")
        return [], f"api_error: {err}"

    html = api_data["parse"]["text"]["*"]
    soup = BeautifulSoup(html, "lxml")

    # Strategy: find ALL wikitables whose header includes "Rank" AND
    # ("Player" OR "Name"). The men's Ballon d'Or ranking is the FIRST
    # such table (or the first TWO for 2010-2015 FIFA merger era).
    # Allow up to 40 cols to handle 2022+ pages with per-country vote columns.
    ranking_tables = []
    for tbl in soup.find_all("table", class_=lambda c: c and "wikitable" in c):
        ths = tbl.find_all("th")
        header_text = " ".join(th.get_text(strip=True) for th in ths[:8]).lower()
        header_text_clean = re.sub(r"\[[^\]]*\]", "", header_text)
        if "rank" in header_text_clean and ("player" in header_text_clean or "name" in header_text_clean):
            ncols = len(ths)
            if ncols <= 40:
                ranking_tables.append(tbl)

    if not ranking_tables:
        return [], "no ranking wikitable found"

    # Parse the first ranking table
    target = ranking_tables[0]
    try:
        parsed = pd.read_html(io.StringIO(str(target)))
    except ValueError as e:
        return [], f"pd.read_html failed: {e}"
    if not parsed:
        return [], "pd.read_html returned no tables"
    df = parsed[0]

    # FIFA merger era (2010-2015): if the first table has only ~3 data rows
    # AND a second ranking table with same column count exists, concatenate.
    # This is the "top 3 + ranks 4+" split pattern.
    concat_status = ""
    if len(df) <= 4 and len(ranking_tables) >= 2:
        try:
            parsed2 = pd.read_html(io.StringIO(str(ranking_tables[1])))
            if parsed2 and len(parsed2[0]) > 0:
                df2 = parsed2[0]
                # Flatten multi-index on df2 if present (same logic as below)
                if isinstance(df2.columns, pd.MultiIndex):
                    new_cols2 = []
                    for top, bottom in df2.columns:
                        top_s = str(top).strip() if top is not None else ""
                        bottom_s = str(bottom).strip() if bottom is not None else ""
                        if "Unnamed" in bottom_s: new_cols2.append(top_s)
                        elif "Unnamed" in top_s: new_cols2.append(bottom_s)
                        else: new_cols2.append(bottom_s if bottom_s else top_s)
                    df2.columns = new_cols2
                # CRITICAL: normalize column names BEFORE concatenating.
                # Otherwise tables with different footnote markers in headers
                # (e.g., "Player[2]" vs "Player[3]") won't align after concat.
                df.columns = normalize_columns(list(df.columns))
                df2.columns = normalize_columns(list(df2.columns))
                # Verify same column count before concatenating
                if df2.shape[1] == df.shape[1]:
                    df = pd.concat([df, df2], ignore_index=True)
                    concat_status = f"; concatenated table[1] (+{len(df2)} rows)"
                else:
                    concat_status = f"; skipped concat: col count mismatch {df2.shape[1]} vs {df.shape[1]}"
            else:
                concat_status = "; skipped concat: table[1] empty"
        except Exception as e:
            concat_status = f"; concat FAILED: {type(e).__name__}: {e}"
    if concat_status:
        # Append to last status message
        pass  # will be added below

    # Drop fully-NaN rows
    df = df.dropna(how="all").reset_index(drop=True)

    # Flatten multi-index columns if present.
    # For tables like 2003/2004 with "Votes by place" super-header over
    # 1st/2nd/3rd/4th/5th vote columns, the multi-index is
    # (top_level, bottom_level). We want only the bottom-level names for
    # vote columns (1st, 2nd, ...) and only the top-level for the main
    # columns (Rank, Player, Nationality, Club, Points).
    if isinstance(df.columns, pd.MultiIndex):
        new_cols = []
        for top, bottom in df.columns:
            top_s = str(top).strip() if top is not None else ""
            bottom_s = str(bottom).strip() if bottom is not None else ""
            # If bottom is "Unnamed: N_level_M", use top
            if "Unnamed" in bottom_s:
                new_cols.append(top_s)
            # If top is "Unnamed: N_level_M", use bottom
            elif "Unnamed" in top_s:
                new_cols.append(bottom_s)
            # If both are meaningful (e.g., ("Votes by place", "1st")), keep bottom
            # because the bottom is the more specific name
            else:
                new_cols.append(bottom_s if bottom_s else top_s)
        df.columns = new_cols

    # Normalize column names
    df.columns = normalize_columns(list(df.columns))

    # Check required columns present
    if "rank" not in df.columns or "player" not in df.columns:
        return [], f"missing rank/player column. cols={list(df.columns)}"

    # Get column references (handles duplicate column names)
    rank_col = get_first_col(df, "rank")
    player_col = get_first_col(df, "player")
    club_col = get_first_col(df, "club")
    nat_col = get_first_col(df, "nationality")
    pos_col = get_first_col(df, "position")
    points_col = get_first_col(df, "points")

    rows: list[dict] = []
    skipped = 0
    for i in range(len(df)):
        rank = parse_rank(rank_col.iloc[i] if rank_col is not None else None)
        if rank is None:
            skipped += 1
            continue
        player = clean_text(player_col.iloc[i] if player_col is not None else None)
        if not player:
            skipped += 1
            continue
        club = clean_text(club_col.iloc[i] if club_col is not None else None)
        nation = clean_text(nat_col.iloc[i] if nat_col is not None else None)
        position = clean_text(pos_col.iloc[i] if pos_col is not None else None)
        points = parse_points(points_col.iloc[i] if points_col is not None else None)
        rows.append({
            "season_id": str(year),
            "award_year": year,
            "rank": rank,
            "player_name_raw": player,
            "club_at_time": club,
            "nation_team": nation,
            "position_raw": position,
            "points": points,
            "source": f"wikipedia_api/{year}_Ballon_d%27Or",
        })
    if not rows:
        return [], f"no rows parsed (skipped {skipped}){concat_status}"
    return rows, f"ok (skipped {skipped} non-parseable rows){concat_status}"


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Ballon d'Or ground truth from Wikipedia Action API")
    parser.add_argument("--start-year", type=int, default=1956, help="Inclusive start year")
    parser.add_argument("--end-year", type=int, default=2025, help="Inclusive end year")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Subset years based on args (still skip 2020)
    scrape_years = [y for y in range(args.start_year, args.end_year + 1) if y != 2020]

    log_lines: list[str] = []
    log_lines.append(f"# Ground-truth scrape log (Action API)\n\n")
    log_lines.append(f"Started: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
    log_lines.append(f"Range: {args.start_year}-{args.end_year} (skipping 2020)\n\n")

    all_rows: list[dict] = []
    per_year_summary: list[tuple] = []  # (year, source, row_count, status)

    print("=" * 70)
    print(f"Scraping {len(scrape_years)} per-year Wikipedia pages via Action API")
    print(f"({args.start_year}-{args.end_year}, skipping 2020 = COVID cancellation)")
    print("=" * 70)

    for i, year in enumerate(scrape_years, 1):
        cache = PAGES_DIR / f"{year}.json"
        try:
            api_data = fetch_api_page(year, cache)
            rows, status = parse_per_year_api(api_data, year)
            all_rows.extend(rows)
            per_year_summary.append((year, "api", len(rows), status))
            marker = "✅" if rows else "⚠️"
            print(f"  [{i:2}/{len(scrape_years)}] {marker} {year}: {len(rows):>3} rows  ({status})")
        except Exception as e:
            per_year_summary.append((year, "api", 0, f"FAILED: {type(e).__name__}: {e}"))
            print(f"  [{i:2}/{len(scrape_years)}] ❌ {year}: FAILED ({type(e).__name__}: {e})")
        time.sleep(REQUEST_DELAY_S)

    # 2020 cancellation: explicit gap entry in the summary log (no row in JSONL)
    for year, reason in CANCELLED_YEARS.items():
        if args.start_year <= year <= args.end_year:
            per_year_summary.append((year, "cancelled", 0, reason))
            print(f"  --- {year}: CANCELLED ({reason})")

    # Always write the combined JSONL from ALL cached years (not just this
    # run's years). This makes the JSONL always reflect the full cache state,
    # regardless of chunking. Per Architecture Blueprint §4.2 idempotency.
    all_cached_rows: list[dict] = []
    for cache_file in sorted(PAGES_DIR.glob("*.json")):
        year_from_name = int(cache_file.stem)
        if year_from_name == 2020:
            continue
        try:
            api_data = json.loads(cache_file.read_text(encoding="utf-8"))
            rows, _ = parse_per_year_api(api_data, year_from_name)
            all_cached_rows.extend(rows)
        except Exception as e:
            print(f"  ⚠️ Failed to re-parse cached {cache_file.name}: {e}")

    tmp_path = OUTPUT_JSONL.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for r in all_cached_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp_path.replace(OUTPUT_JSONL)
    print()
    print(f"✅ Wrote {len(all_cached_rows)} total rows (from {len(list(PAGES_DIR.glob('*.json')))} cached pages) to {OUTPUT_JSONL}")
    all_rows = all_cached_rows  # for the sanity stats below

    # Append per-year summary to scrape log (append mode if file exists)
    mode = "a" if SCRAPE_LOG.exists() else "w"
    with open(SCRAPE_LOG, mode, encoding="utf-8") as f:
        f.write(f"\n## Run {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} — range {args.start_year}-{args.end_year}\n\n")
        f.write(f"| Year | Source | Rows | Status |\n|---|---|---|---|\n")
        for y, src, cnt, status in per_year_summary:
            f.write(f"| {y} | {src} | {cnt} | {status} |\n")
        f.write(f"\n**Run total rows:** {len(all_rows)}\n")
        f.write(f"\nFinished: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
    print(f"✅ Appended run summary to {SCRAPE_LOG}")

    # Quick sanity stats for this run
    print()
    print("=" * 70)
    print(f"Sanity stats (this run, range {args.start_year}-{args.end_year})")
    print("=" * 70)
    if all_rows:
        df = pd.DataFrame(all_rows)
        print(f"  Total rows: {len(df)}")
        print(f"  Unique years: {df['award_year'].nunique()}")
        if df["award_year"].nunique() > 0:
            print(f"  Year range: {df['award_year'].min()}-{df['award_year'].max()}")
            sizes = df.groupby("award_year").size()
            print(f"  Rows per year: min={sizes.min()}, median={sizes.median():.0f}, max={sizes.max()}, mean={sizes.mean():.1f}")
            winners = df[df["rank"] == 1]
            print(f"  Total winners (rank=1) in this run: {len(winners)}")
            dup_winners = winners[winners["award_year"].duplicated(keep=False)]
            if len(dup_winners):
                print(f"  ⚠️ Years with multiple rank=1: {sorted(dup_winners['award_year'].unique().tolist())}")


if __name__ == "__main__":
    main()
