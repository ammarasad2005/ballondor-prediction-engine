"""Player stats scraper for the Ballon d'Or Prediction Engine.

Per Architecture Blueprint §4.2 + §4.4 family 1, this scraper pulls
individual performance data for each player in the ground truth table.

Source: Wikipedia player pages via the Action API.
  https://en.wikipedia.org/w/api.php?action=parse&prop=text&page={player_slug}&format=json

Coverage:
  - All eras use the same source (Wikipedia player pages) — per Phase 0
    finding, fbref (xG/xA) is permanently Cloudflare-blocked in this
    sandbox, and Understat requires full JS execution we don't have.
  - Modern era (2014-15+): targets goals/assists/apps/minutes per
    competition (League, Continental, National Team). xG/xA documented
    as permanent gap.
  - Classical era (1956-2014): same target per P4 — goals/assists/apps.
    Assists pre-1990s are notoriously poorly recorded; documented gaps
    will be flagged with _is_imputed=False / _is_missing=True per
    Key Focus Areas §9.

Idempotency (Architecture Blueprint §4.2):
  - Each player's Action API response is cached to
    data/raw/stats/pages_api/{player_slug}.json
  - Parsed stats are written to data/raw/stats/stats_raw.jsonl keyed
    on (season_id, player_name_raw). Re-runs skip already-parsed keys.

Disambiguation strategy (Phase 2 scope):
  - For each player_name_raw, slugify (replace spaces with underscores,
    preserve diacritics). Try fetching that page.
  - If page exists AND has a "Senior career" stats table, parse it.
  - If page does not exist (HTTP 404 / API error) OR has no stats
    table, log the slug to data/raw/stats/_failed_lookups.txt for
    Phase 3 entity resolution to handle (alias table, fuzzy match).
  - Phase 3 will populate alias_table.yaml with corrected slugs for
    failed lookups (e.g., "Ronaldo" → "Ronaldo (Brazilian footballer)"
    vs "Cristiano Ronaldo"). For Phase 2, we just record the failures.

Output schema (raw, pre-feature-engineering):
  {
    "season_id": "2023",
    "player_name_raw": "Lionel Messi",
    "player_slug": "Lionel_Messi",
    "wiki_page_title": "Lionel Messi",
    "stats_source": "wikipedia_api",
    "career_stats": [
      {
        "season": "2022-23",
        "club": "Paris Saint-Germain",
        "league_apps": 32,
        "league_goals": 16,
        "league_assists": 16,
        "league_minutes": 2778,
        "continental_apps": 7,
        "continental_goals": 4,
        "continental_assists": 4,
        "continental_minutes": 630,
        "continental_competition": "Champions League",
        "national_team_apps": 7,
        "national_team_goals": 7,
        "national_team_assists": 3
      },
      ...
    ]
  }
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
GROUND_TRUTH_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "stats"
PAGES_DIR = RAW_DIR / "pages_api"
OUTPUT_JSONL = RAW_DIR / "stats_raw.jsonl"
FAILED_LOOKUPS = RAW_DIR / "_failed_lookups.txt"
SCRAPE_LOG = RAW_DIR / "scrape_log.md"

USER_AGENT = "BallonDorPredictBot/0.1 (research; contact: agent@local)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
REQUEST_DELAY_S = 1.5
TIMEOUT_S = 30
MAX_RETRIES = 3

API_URL = "https://en.wikipedia.org/w/api.php"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

FOOTNOTE_RE = re.compile(r"\s*\[[^\]]*\]\s*")


def slugify(name: str) -> str:
    """Convert a player name to a Wikipedia URL slug.

    Wikipedia slugs use underscores for spaces and preserve diacritics.
    Examples:
      "Lionel Messi" -> "Lionel_Messi"
      "Cristiano Ronaldo" -> "Cristiano_Ronaldo"
      "Andrés Iniesta" -> "Andr%C3%A9s_Iniesta" (URL-encoded)
      "Pelé" -> "Pel%C3%A9"
    For the Action API, we don't need to URL-encode — just replace spaces
    with underscores. The API handles the encoding internally.
    """
    if not name:
        return ""
    # Replace spaces with underscores
    slug = re.sub(r"\s+", "_", name.strip())
    # Strip trailing footnote markers
    slug = FOOTNOTE_RE.sub("", slug)
    return slug


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


# -----------------------------------------------------------------------------
# Stats table parsing
# -----------------------------------------------------------------------------

# Year-range pattern in season column: "2022-23", "2022–23", "2022/23", "2022-2023"
SEASON_PATTERN = re.compile(r"^(\d{4})[\-–/](\d{2,4})$")


def normalize_season(s) -> Optional[str]:
    """Normalize a season string like '2022-23' -> '2022-2023' (full year).
    Also strips Wikipedia footnote markers like '2003-04[437]' first.
    Returns None if not a season format.
    """
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    # Strip footnote markers like '[437]', '[note 1]' FIRST
    s = FOOTNOTE_RE.sub("", s).strip()
    m = SEASON_PATTERN.match(s)
    if not m:
        # Single year (classical era) — return as-is
        if re.match(r"^\d{4}$", s):
            return s
        return None
    start_year = int(m.group(1))
    end_year_raw = m.group(2)
    if len(end_year_raw) == 2:
        century = start_year // 100
        end_year = century * 100 + int(end_year_raw)
        if end_year < start_year:
            end_year += 100
    else:
        end_year = int(end_year_raw)
    return f"{start_year}-{end_year}"


def parse_int_safe(v) -> Optional[int]:
    """Parse an integer from various string forms. Returns None on failure."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if s in ("", "-", "—", "nan", "None"):
        return None
    # Some cells have footnote markers
    s = FOOTNOTE_RE.sub("", s).strip()
    # Some cells have non-numeric like "On loan" — return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def parse_minutes(v) -> Optional[int]:
    """Parse minutes — typically a number, sometimes with comma."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace(",", "")
    s = FOOTNOTE_RE.sub("", s).strip()
    if s in ("", "-", "—", "nan", "None"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def find_senior_career_table(soup: BeautifulSoup):
    """Find the 'Senior career' stats table on a player's Wikipedia page.

    Player pages have a variety of structures across eras:
      - Modern pages: caption "Senior career*" with columns
        Years/Team/League{Division,Apps,Goals}/Continental{...}
      - Classical-era pages: caption "Appearances and goals by club,
        season and competition" with columns
        Club/Season/League{Division,Apps}/National cup{Apps}/...
      - Some pages: header row with "Years" / "Team" / "Apps" / "Goals"

    Strategy: try multiple detection patterns in order of reliability.
    Note: some pages have stats tables WITHOUT the "wikitable" CSS class
    (older pages use plain <table> with inline styling), so we search
    all tables, not just wikitables.
    """
    # Get ALL tables, then filter to those that look like career tables.
    # We exclude obvious non-career tables: infoboxes, navboxes, etc.
    def is_career_candidate(tbl) -> bool:
        cls = tbl.get("class") or []
        # Exclude navboxes
        if "navbox" in cls or "nowraplinks" in cls:
            return False
        # Exclude infoboxes (they have player bio info, not stats)
        if "infobox" in cls:
            return False
        return True

    all_tables = [t for t in soup.find_all("table") if is_career_candidate(t)]

    # Strategy 1: caption containing "senior career" OR "appearances and goals"
    for tbl in all_tables:
        cap = tbl.find("caption")
        if cap:
            cap_text = cap.get_text(strip=True).lower()
            if "senior career" in cap_text:
                return tbl
            if "appearances and goals by club" in cap_text:
                return tbl
            if "appearances and goals by national" in cap_text:
                continue   # this is the international table
        # Or look at the first row's first cell
        first_tr = tbl.find("tr")
        if first_tr:
            first_th = first_tr.find("th") or first_tr.find("td")
            if first_th:
                first_text = first_th.get_text(strip=True).lower()
                if "senior career" in first_text:
                    return tbl

    # Strategy 2: header row containing (Years OR Season OR Club) AND
    # (Apps OR Appearances OR Goals OR Division).
    intl_table = find_international_career_table(soup)
    for tbl in all_tables:
        if tbl is intl_table:
            continue
        ths = tbl.find_all("th")
        if not ths:
            continue
        header_text = " ".join(th.get_text(strip=True) for th in ths[:12]).lower()
        header_text_clean = re.sub(r"\[[^\]]*\]", "", header_text)
        has_years_or_season = ("years" in header_text_clean or "season" in header_text_clean)
        has_team_or_club = ("team" in header_text_clean or "club" in header_text_clean)
        has_apps_or_goals = (
            "apps" in header_text_clean or "appearances" in header_text_clean or
            "goals" in header_text_clean or "division" in header_text_clean or
            "matches" in header_text_clean
        )
        if has_years_or_season and has_team_or_club and has_apps_or_goals:
            if "international" in header_text_clean or "national team" in header_text_clean:
                continue
            return tbl

    return None


def find_international_career_table(soup: BeautifulSoup):
    """Find the international career stats table (separate from senior club career)."""
    def is_candidate(tbl) -> bool:
        cls = tbl.get("class") or []
        if "navbox" in cls or "nowraplinks" in cls or "infobox" in cls:
            return False
        return True
    all_tables = [t for t in soup.find_all("table") if is_candidate(t)]

    # Strategy 1: caption
    for tbl in all_tables:
        cap = tbl.find("caption")
        if cap:
            cap_text = cap.get_text(strip=True).lower()
            if "appearances and goals by national" in cap_text:
                return tbl
            if "international" in cap_text and "goals" in cap_text:
                return tbl
    # Strategy 2: header
    for tbl in all_tables:
        ths = tbl.find_all("th")
        if not ths:
            continue
        header_text = " ".join(th.get_text(strip=True) for th in ths[:8]).lower()
        if "national team" in header_text or "international career" in header_text:
            return tbl
    return None


def _parse_career_table_fallback(tbl) -> list[dict]:
    """Fallback parser using BeautifulSoup directly when pd.read_html fails.

    Used when the HTML has malformed attributes (e.g. colspan='2"') that
    pd.read_html can't handle. This parser is less robust but handles
    the common case of a stats table with rows like:
      <tr><td>2023-24</td><td>Manchester City</td><td>35</td><td>27</td>...</tr>
    """
    rows: list[dict] = []
    # Find all data rows (skip header rows)
    for tr in tbl.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells or len(cells) < 3:
            continue
        # Skip header rows (all th)
        if all(c.name == "th" for c in cells):
            continue
        # First cell should be a season string
        first = clean_text(cells[0].get_text())
        if not first:
            continue
        season = normalize_season(first)
        if season is None:
            continue
        # Skip aggregate rows
        first_lower = first.lower()
        if "total" in first_lower or "career" in first_lower:
            continue
        # Build a row with whatever cells we have
        row = {
            "season": season,
            "season_raw": first,
            "club": clean_text(cells[1].get_text()) if len(cells) > 1 else "",
            "league_apps": parse_int_safe(cells[2].get_text()) if len(cells) > 2 else None,
            "league_goals": parse_int_safe(cells[3].get_text()) if len(cells) > 3 else None,
            "league_assists": None,
            "league_minutes": None,
            "continental_apps": None,
            "continental_goals": None,
            "continental_assists": None,
            "continental_minutes": None,
            "continental_competition": "",
        }
        rows.append(row)
    return rows


def parse_career_table(tbl) -> list[dict]:
    """Parse a senior career table into a list of season-row dicts.

    The table typically has multi-index columns:
      (Years, Years)  (Team, Team)  (League, Division)  (League, Apps)  (League, Goals)
      (Continental, Apps)  (Continental, Goals)  ...

    We flatten the multi-index and extract:
      season (normalized), club, league_apps, league_goals, league_assists,
      league_minutes, continental_apps, continental_goals, continental_assists,
      continental_minutes, continental_competition
    """
    # Pre-clean the HTML: fix malformed colspan/rowspan attributes that
    # contain stray characters (e.g. colspan='2"' instead of '2').
    # pd.read_html raises ValueError on these otherwise.
    html_str = str(tbl)
    html_str = re.sub(r'(colspan|rowspan)=["\']?(\d+)["\']?[^>]*', r'\1="\2"', html_str, flags=re.IGNORECASE)
    html_str = re.sub(r'(colspan|rowspan)=["\'](\d+)[^"\']*', r'\1="\2"', html_str, flags=re.IGNORECASE)

    try:
        parsed = pd.read_html(io.StringIO(html_str))
        if not parsed:
            return []
        df = parsed[0]
    except Exception as e:
        # If pd.read_html still fails, try a simpler approach: extract
        # rows manually using BeautifulSoup
        return _parse_career_table_fallback(tbl)

    # Drop fully-NaN rows
    df = df.dropna(how="all").reset_index(drop=True)

    # Flatten multi-index columns
    if isinstance(df.columns, pd.MultiIndex):
        new_cols = []
        for col_tuple in df.columns:
            # Handle 2-level and 3-level multi-indexes
            parts = []
            for level in col_tuple:
                s = str(level).strip() if level is not None else ""
                if "Unnamed" in s or s == "nan" or s == "":
                    continue
                parts.append(s)
            if not parts:
                new_cols.append("")
            elif len(parts) == 1:
                new_cols.append(parts[0])
            else:
                # If all parts are the same, use just one; else concatenate
                if all(p == parts[0] for p in parts):
                    new_cols.append(parts[0])
                else:
                    new_cols.append(" ".join(parts).strip())
        df.columns = new_cols
    else:
        df.columns = [str(c).strip() for c in df.columns]

    # Normalize column names — lowercase, strip footnotes
    df.columns = [re.sub(r"\[[^\]]*\]", "", str(c)).strip().lower() for c in df.columns]

    # Find the season column (could be "years", "season", "year")
    season_col = None
    for c in df.columns:
        if c in ("years", "season", "year"):
            season_col = c
            break
    if season_col is None:
        return []

    # Find other columns
    club_col = None
    for c in df.columns:
        if c in ("team", "club", "clubs"):
            club_col = c
            break

    # League stats columns — under "League Division" / "League Apps" / "League Goals"
    # Sometimes there's no "League" prefix; in that case, bare "Apps"/"Goals" are league.
    league_apps_col = None
    league_goals_col = None
    league_assists_col = None
    league_minutes_col = None
    continental_apps_col = None
    continental_goals_col = None
    continental_assists_col = None
    continental_minutes_col = None
    continental_competition_col = None

    for c in df.columns:
        c_lower = c.lower()
        # Continental competition name column
        # Note: some pages use "Europe" or "European" instead of "Continental"
        is_continental = ("continental" in c_lower or "europe" in c_lower)
        if is_continental and ("competition" in c_lower or "cup" in c_lower or "tournament" in c_lower or "name" in c_lower or "type" in c_lower):
            if continental_competition_col is None:
                continental_competition_col = c
        elif is_continental and ("apps" in c_lower or "appearances" in c_lower or "matches" in c_lower or " mp" in c_lower):
            if continental_apps_col is None:
                continental_apps_col = c
        elif is_continental and ("goals" in c_lower or "gls" in c_lower):
            if continental_goals_col is None:
                continental_goals_col = c
        elif is_continental and "assists" in c_lower:
            if continental_assists_col is None:
                continental_assists_col = c
        elif is_continental and "min" in c_lower:
            if continental_minutes_col is None:
                continental_minutes_col = c
        # League columns — must check AFTER continental
        elif "league" in c_lower and ("apps" in c_lower or "appearances" in c_lower or "matches" in c_lower or " mp" in c_lower):
            if league_apps_col is None:
                league_apps_col = c
        elif "league" in c_lower and ("goals" in c_lower or "gls" in c_lower):
            if league_goals_col is None:
                league_goals_col = c
        elif "league" in c_lower and "assists" in c_lower:
            if league_assists_col is None:
                league_assists_col = c
        elif "league" in c_lower and "min" in c_lower:
            if league_minutes_col is None:
                league_minutes_col = c
        # Bare columns (no League/Continental prefix) — typically league stats
        # Only assign if no league_* has been set yet (avoid overwriting real
        # league columns with bare ones)
        elif league_apps_col is None and c_lower in ("apps", "appearances", "matches", "mp"):
            league_apps_col = c
        elif league_goals_col is None and c_lower in ("goals", "gls"):
            league_goals_col = c
        elif league_assists_col is None and c_lower in ("assists", "ast"):
            league_assists_col = c
        elif league_minutes_col is None and c_lower in ("minutes", "min", "mins"):
            league_minutes_col = c

    rows = []
    for _, r in df.iterrows():
        season_raw = r.get(season_col)
        season = normalize_season(season_raw)
        if season is None:
            continue   # skip header rows or non-season data

        # Skip aggregate rows ("Career total", "Total", "Total with Barcelona", etc.)
        season_str = str(season_raw).strip().lower() if season_raw is not None else ""
        if "total" in season_str or "career" in season_str:
            continue

        # Also check the club column for "Total"/"Career total" labels
        if club_col:
            club_val = str(r.get(club_col, "")).strip().lower()
            if "career" in club_val or "total" in club_val:
                continue

        row = {
            "season": season,
            "season_raw": str(season_raw).strip() if season_raw is not None else "",
            "club": str(r.get(club_col, "")).strip() if club_col else "",
            "league_apps": parse_int_safe(r.get(league_apps_col)) if league_apps_col else None,
            "league_goals": parse_int_safe(r.get(league_goals_col)) if league_goals_col else None,
            "league_assists": parse_int_safe(r.get(league_assists_col)) if league_assists_col else None,
            "league_minutes": parse_minutes(r.get(league_minutes_col)) if league_minutes_col else None,
            "continental_apps": parse_int_safe(r.get(continental_apps_col)) if continental_apps_col else None,
            "continental_goals": parse_int_safe(r.get(continental_goals_col)) if continental_goals_col else None,
            "continental_assists": parse_int_safe(r.get(continental_assists_col)) if continental_assists_col else None,
            "continental_minutes": parse_minutes(r.get(continental_minutes_col)) if continental_minutes_col else None,
            "continental_competition": str(r.get(continental_competition_col, "")).strip() if continental_competition_col else "",
        }
        rows.append(row)
    return rows


def parse_international_table(tbl) -> list[dict]:
    """Parse international career table. Columns vary; typical:
       Year | Team | Apps | Goals
    Returns list of {year, team, apps, goals}.
    """
    try:
        parsed = pd.read_html(io.StringIO(str(tbl)))
        if not parsed:
            return []
        df = parsed[0]
    except ValueError:
        return []

    df = df.dropna(how="all").reset_index(drop=True)
    if isinstance(df.columns, pd.MultiIndex):
        new_cols = []
        for col_tuple in df.columns:
            parts = []
            for level in col_tuple:
                s = str(level).strip() if level is not None else ""
                if "Unnamed" in s or s == "nan" or s == "":
                    continue
                parts.append(s)
            if not parts:
                new_cols.append("")
            elif len(parts) == 1:
                new_cols.append(parts[0])
            else:
                if all(p == parts[0] for p in parts):
                    new_cols.append(parts[0])
                else:
                    new_cols.append(" ".join(parts).strip())
        df.columns = new_cols
    else:
        df.columns = [str(c).strip() for c in df.columns]
    df.columns = [re.sub(r"\[[^\]]*\]", "", str(c)).strip().lower() for c in df.columns]

    year_col = None
    for c in df.columns:
        if c in ("year", "years"):
            year_col = c
            break
    if year_col is None:
        return []

    team_col = None
    for c in df.columns:
        if c in ("team", "national team"):
            team_col = c
            break

    apps_col = None
    goals_col = None
    for c in df.columns:
        cl = c.lower()
        if apps_col is None and cl in ("apps", "appearances", "matches", "mp", "caps"):
            apps_col = c
        elif goals_col is None and cl in ("goals", "gls"):
            goals_col = c

    rows = []
    for _, r in df.iterrows():
        year_raw = r.get(year_col)
        if year_raw is None or (isinstance(year_raw, float) and pd.isna(year_raw)):
            continue
        year_str = str(year_raw).strip()
        # Year column may be a single year or a range like "2005-2024"
        # For international stats, we typically want a single year row
        if not re.match(r"^\d{4}", year_str):
            continue
        # Skip aggregate rows
        if team_col:
            team_val = str(r.get(team_col, "")).strip().lower()
            if "career" in team_val or "total" in team_val:
                continue
        rows.append({
            "year": year_str,
            "team": str(r.get(team_col, "")).strip() if team_col else "",
            "apps": parse_int_safe(r.get(apps_col)) if apps_col else None,
            "goals": parse_int_safe(r.get(goals_col)) if goals_col else None,
        })
    return rows


# -----------------------------------------------------------------------------
# Main scrape logic
# -----------------------------------------------------------------------------


def load_unique_players(start_year: int, end_year: int) -> pd.DataFrame:
    """Load ground truth and return unique (player_name_raw, season_id, award_year) tuples
    within the given year range.
    """
    df = pd.read_parquet(GROUND_TRUTH_PARQUET)
    df = df[(df["award_year"] >= start_year) & (df["award_year"] <= end_year)]
    return df[["season_id", "award_year", "player_name_raw", "club_at_time", "nation_team", "position_raw"]]


def scrape_player(player_name_raw: str) -> dict:
    """Scrape a single player's stats from Wikipedia.

    Returns dict with:
      player_name_raw, player_slug, wiki_page_title, stats_source,
      career_stats (list), international_stats (list), status, error
    """
    slug = slugify(player_name_raw)
    cache_path = PAGES_DIR / f"{slug}.json"

    try:
        api_data = fetch_api_page(slug, cache_path)
    except Exception as e:
        return {
            "player_name_raw": player_name_raw,
            "player_slug": slug,
            "status": "fetch_failed",
            "error": f"{type(e).__name__}: {e}",
            "career_stats": [],
            "international_stats": [],
        }

    if "parse" not in api_data:
        err = api_data.get("error", {}).get("info", "missing 'parse' key")
        return {
            "player_name_raw": player_name_raw,
            "player_slug": slug,
            "status": "page_missing",
            "error": err,
            "career_stats": [],
            "international_stats": [],
        }

    html = api_data["parse"]["text"]["*"]
    page_title = api_data["parse"]["title"]
    soup = BeautifulSoup(html, "lxml")

    career_tbl = find_senior_career_table(soup)
    intl_tbl = find_international_career_table(soup)

    career_stats = parse_career_table(career_tbl) if career_tbl else []
    intl_stats = parse_international_table(intl_tbl) if intl_tbl else []

    status = "ok" if career_stats else "no_career_table"
    return {
        "player_name_raw": player_name_raw,
        "player_slug": slug,
        "wiki_page_title": page_title,
        "stats_source": "wikipedia_api",
        "status": status,
        "career_stats": career_stats,
        "international_stats": intl_stats,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape player stats from Wikipedia")
    parser.add_argument("--start-year", type=int, default=1956)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--limit", type=int, default=None, help="Max players to scrape (for testing)")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Load unique players within range
    df_gt = load_unique_players(args.start_year, args.end_year)
    unique_players = df_gt["player_name_raw"].unique().tolist()
    print(f"Ground truth range {args.start_year}-{args.end_year}: {len(df_gt)} rows, {len(unique_players)} unique players")

    if args.limit:
        unique_players = unique_players[:args.limit]
        print(f"Limited to first {args.limit} players for testing")

    # Load already-scraped players (idempotency)
    scraped = set()
    if OUTPUT_JSONL.exists():
        with open(OUTPUT_JSONL, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    scraped.add(r["player_name_raw"])
                except json.JSONDecodeError:
                    continue
    print(f"Already scraped: {len(scraped)}")
    todo = [p for p in unique_players if p not in scraped]
    print(f"To scrape: {len(todo)}")

    # Also load failed lookups (to skip ones we already know fail)
    failed = set()
    if FAILED_LOOKUPS.exists():
        with open(FAILED_LOOKUPS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    failed.add(line.split("\t")[0] if "\t" in line else line)
    todo_failed_already = [p for p in todo if p in failed]
    print(f"Already known to fail (skipping): {len(todo_failed_already)}")
    todo = [p for p in todo if p not in failed]

    # Append-mode output
    out_f = open(OUTPUT_JSONL, "a", encoding="utf-8")
    failed_f = open(FAILED_LOOKUPS, "a", encoding="utf-8")

    n_ok = 0
    n_no_table = 0
    n_fetch_fail = 0
    n_page_missing = 0

    print()
    print("=" * 70)
    print(f"Scraping {len(todo)} players via Wikipedia Action API")
    print("=" * 70)

    for i, player in enumerate(todo, 1):
        result = scrape_player(player)
        out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
        out_f.flush()

        status = result["status"]
        if status == "ok":
            n_ok += 1
            n_seasons = len(result["career_stats"])
            marker = "✅"
        elif status == "no_career_table":
            n_no_table += 1
            failed_f.write(f"{player}\t{status}\n")
            failed_f.flush()
            marker = "⚠️"
        elif status == "fetch_failed":
            n_fetch_fail += 1
            failed_f.write(f"{player}\t{status}\t{result.get('error', '')[:100]}\n")
            failed_f.flush()
            marker = "❌"
        elif status == "page_missing":
            n_page_missing += 1
            failed_f.write(f"{player}\t{status}\t{result.get('error', '')[:100]}\n")
            failed_f.flush()
            marker = "❌"

        print(f"  [{i:4}/{len(todo)}] {marker} {player!r}: {status}" +
              (f" ({n_seasons} seasons)" if status == "ok" else
               f" — {result.get('error', '')[:60]}" if status != "ok" else ""))

        time.sleep(REQUEST_DELAY_S)

    out_f.close()
    failed_f.close()

    print()
    print("=" * 70)
    print(f"Done. OK={n_ok}  no_career_table={n_no_table}  fetch_failed={n_fetch_fail}  page_missing={n_page_missing}")
    print(f"Output: {OUTPUT_JSONL}")
    print(f"Failed lookups: {FAILED_LOOKUPS}")


if __name__ == "__main__":
    main()
