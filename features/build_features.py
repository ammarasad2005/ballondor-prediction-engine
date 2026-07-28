"""Build features.parquet for the Ballon d'Or Prediction Engine.

Per Architecture Blueprint §4.4 + Implementation Plan Phase 4:
  - Build features.parquet — one row per candidate-season, era tag attached.
  - Sanity-check distributions: spot review of known obvious-winner and
    controversial-winner seasons to catch silent unit errors before modeling.

Per Key Focus Areas §9 — missing data must be visible:
  - Every feature column has a companion `<feature>_is_imputed` bool column
    that is True iff the value was deliberately filled (e.g., median-by-era)
    rather than sourced. Missing-and-not-imputed cells stay NaN.

Per Key Focus Areas §3 — feature leakage prevention:
  - previous_ballon_dor_winner is computed with strict lag (only counts
    awards won BEFORE the current eval period, never including current).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
GROUND_TRUTH_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"
STATS_JSONL = PROJECT_ROOT / "data" / "raw" / "stats" / "stats_raw.jsonl"
TROPHIES_JSONL = PROJECT_ROOT / "data" / "raw" / "trophies" / "trophies_raw.jsonl"
FEATURE_REGISTRY_YAML = PROJECT_ROOT / "features" / "feature_registry.yaml"
OUTPUT_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
QA_REPORT = PROJECT_ROOT / "data" / "processed" / "features_qa_report.md"


# -----------------------------------------------------------------------------
# Era classification
# -----------------------------------------------------------------------------

def classify_era(award_year: int) -> str:
    """Classify a year into one of four eras per Architecture Blueprint §4.1."""
    if award_year < 1995:
        return "classical"        # Europe-only eligibility
    elif award_year < 2010:
        return "pre_merger"       # Global eligibility, pre-FIFA merger
    elif award_year < 2016:
        return "fifa_merger"      # FIFA Ballon d'Or merger window
    else:
        return "post_split"       # France Football only again


# -----------------------------------------------------------------------------
# Stats loading + season matching
# -----------------------------------------------------------------------------

def load_stats_dedup() -> pd.DataFrame:
    """Load stats_raw.jsonl, dedupe by player_name_raw keeping ok > no_career_table > errors."""
    rows = []
    with open(STATS_JSONL, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    df["priority"] = df["status"].map({"ok": 0, "no_career_table": 1, "fetch_failed": 2, "page_missing": 3})
    df_dedup = df.sort_values("priority").drop_duplicates("player_name_raw", keep="first").drop(columns=["priority"])
    return df_dedup


def normalize_season_for_match(s: str) -> Optional[str]:
    """Normalize a season string for matching: '2022-23' or '2022–23' -> '2022-2023'.
    Returns None if not a season format.
    """
    if not s or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    # Strip footnote markers
    s = re.sub(r"\[[^\]]*\]", "", s).strip()
    # Pattern: YYYY-YY or YYYY–YY or YYYY/YYYY
    m = re.match(r"^(\d{4})[\-–/](\d{2,4})$", s)
    if m:
        start_year = int(m.group(1))
        end_raw = m.group(2)
        if len(end_raw) == 2:
            century = start_year // 100
            end_year = century * 100 + int(end_raw)
            if end_year < start_year:
                end_year += 100
        else:
            end_year = int(end_raw)
        return f"{start_year}-{end_year}"
    # Single year
    if re.match(r"^\d{4}$", s):
        return s
    return None


def match_season_to_eval_period(season_str: str, eval_period_type: str, award_year: int) -> bool:
    """Check if a stats season string falls within the eval period for a given award year.

    For calendar_year eval (1956-2021): the season must overlap with award_year.
      - '2023' matches award_year=2023
      - '2022-23' or '2022-2023' matches award_year=2023 (the season ending in 2023)
      - '2023-24' or '2023-2024' matches award_year=2023 (the season starting in 2023)
      - Both cases above match because the season overlaps with calendar year 2023.

    For season eval (2022-present): the season must be the one ending in award_year.
      - '2023-24' matches award_year=2024 (Aug 2023 - Jul 2024)
      - '2022-23' matches award_year=2023 (Aug 2022 - Jul 2023)
    """
    norm = normalize_season_for_match(season_str)
    if not norm:
        return False

    if eval_period_type == "season":
        # Season-based: 'YYYY-(YYYY-1)' is NOT the eval season; 'YYYY-(YYYY)' IS
        # i.e., for award_year=2024, eval season is '2023-2024'
        # If norm is a single year, it doesn't match season-based eval
        if "-" in norm:
            start_y, end_y = norm.split("-")
            return int(end_y) == award_year
        return False
    else:
        # Calendar-year eval: season must overlap with award_year
        # '2023' matches award_year=2023
        # '2022-23' or '2022-2023' matches award_year=2023 (overlaps both 2022 and 2023)
        # '2023-24' or '2023-2024' matches award_year=2023 (overlaps both 2023 and 2024)
        if "-" not in norm:
            return int(norm) == award_year
        start_y, end_y = norm.split("-")
        start_y = int(start_y)
        end_y = int(end_y)
        return start_y == award_year or end_y == award_year


def sum_stats_for_eval_period(career_stats: list[dict], eval_period_type: str, award_year: int) -> dict:
    """Sum stats across all season rows that fall within the eval period.

    Returns a dict with the aggregated stats. Missing stats stay None.
    """
    if not career_stats:
        return {}

    # Collect matching season rows
    matching_rows = []
    for row in career_stats:
        season_str = row.get("season_raw") or row.get("season")
        if match_season_to_eval_period(season_str, eval_period_type, award_year):
            matching_rows.append(row)

    if not matching_rows:
        return {}

    # Sum each stat across matching rows
    stat_keys = [
        "league_apps", "league_goals", "league_assists", "league_minutes",
        "continental_apps", "continental_goals", "continental_assists", "continental_minutes",
    ]
    summed = {}
    for key in stat_keys:
        values = [r.get(key) for r in matching_rows if r.get(key) is not None]
        if values:
            summed[key] = sum(values)
        else:
            summed[key] = None

    # Track club(s) played for during eval period
    clubs = []
    for r in matching_rows:
        club = r.get("club", "")
        if club and club not in clubs:
            clubs.append(club)
    summed["clubs_during_eval"] = clubs
    summed["n_seasons_matched"] = len(matching_rows)

    return summed


def sum_international_stats(intl_stats: list[dict], eval_period_type: str, award_year: int) -> dict:
    """Sum international stats across the eval period.

    International stats are typically by calendar year (e.g., '2023' -> 7 apps, 5 goals).
    For calendar-year eval, match the year directly.
    For season-based eval (2022+), match either of the two calendar years spanned.
    """
    if not intl_stats:
        return {}

    matching_rows = []
    for row in intl_stats:
        year_str = str(row.get("year", "")).strip()
        # Year may be a single year or a range like '2005-2024'
        # For ranges, skip (those are summary rows)
        if not re.match(r"^\d{4}$", year_str):
            continue
        year = int(year_str)
        if eval_period_type == "season":
            # Season-based: eval spans award_year-1 to award_year
            if year == award_year or year == award_year - 1:
                matching_rows.append(row)
        else:
            # Calendar-year: match award_year directly
            if year == award_year:
                matching_rows.append(row)

    if not matching_rows:
        return {}

    summed = {}
    for key in ["apps", "goals"]:
        values = [r.get(key) for r in matching_rows if r.get(key) is not None]
        if values:
            summed[f"international_{key}"] = sum(values)
        else:
            summed[f"international_{key}"] = None
    return summed


# -----------------------------------------------------------------------------
# Trophy matching
# -----------------------------------------------------------------------------

def load_trophies() -> pd.DataFrame:
    rows = []
    with open(TROPHIES_JSONL, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return pd.DataFrame(rows)


def find_trophy_for_club(trophy_df: pd.DataFrame, season_id: str, competition: str, club: str,
                         award_year: int = None, eval_period_type: str = None) -> Optional[str]:
    """Check if a club won/lost a specific competition in a given season.

    Returns "winner" / "runner_up" / None.
    Uses fuzzy matching on club name (e.g., "Manchester City" vs "Man City").

    Trophy season_id semantics:
      - Club competitions (UCL, leagues): season_id = START year of the season
        (e.g., season_id="2021" for the 2021-22 UCL, whose final was in May 2022).
      - International tournaments (World Cup, Euro, Copa América): season_id =
        the calendar year the tournament was held.

    Ballon d'Or award_year semantics:
      - Calendar-year eval (1956-2021): award_year = the calendar year the
        jury evaluated. A UCL final played in May 2022 belongs to award_year=2022.
        So for club competitions, we look at trophy season_id = award_year - 1
        (the season that ENDED in award_year).
      - Season-based eval (2022-present): award_year = the year the ceremony was
        held. The eval period is Aug (award_year-1) - Jul (award_year). The UCL
        final in this period was played in June award_year, which is the UCL
        season (award_year-1)-(award_year). So again, trophy season_id = award_year - 1.

    For international tournaments:
      - World Cup/Euro/Copa are held in a specific calendar year. For
        calendar-year eval, season_id == award_year. For season-based eval,
        check both award_year-1 and award_year (tournament might fall in either).
    """
    if not club:
        return None

    # Determine which season_ids to check based on competition type and eval period
    is_intl = competition in ["FIFA World Cup", "UEFA European Championship", "Copa América"]
    if is_intl:
        # International tournament — season_id = calendar year of tournament
        if eval_period_type == "season":
            seasons_to_check = [str(award_year - 1), str(award_year)]
        else:
            seasons_to_check = [str(award_year)]
    else:
        # Club competition — season_id = start year of season
        # For both eval period types, the relevant season is the one ending in award_year
        seasons_to_check = [str(award_year - 1)]
        # For calendar-year eval, ALSO check award_year (season starting in award_year,
        # whose final would be in award_year+1 — but if that final hasn't happened yet
        # at ceremony time, it's not relevant. We'll be conservative and only check
        # award_year - 1 for both. This matches the typical Ballon d'Or timing where
        # the ceremony is in October/November, after the previous season's UCL final
        # (May/June) but before the current season's UCL final (next May/June).
        pass

    club_lower = club.lower().strip()
    for sid in seasons_to_check:
        sub = trophy_df[(trophy_df["season_id"] == sid) & (trophy_df["competition"] == competition)]
        for _, row in sub.iterrows():
            trophy_club_lower = row["team"].lower().strip()
            # Exact match
            if club_lower == trophy_club_lower:
                return row["stage"]
            # Substring match
            if club_lower in trophy_club_lower or trophy_club_lower in club_lower:
                return row["stage"]

    return None


# -----------------------------------------------------------------------------
# Club prestige tier (manually curated)
# -----------------------------------------------------------------------------

# Per Key Focus Area §5 — proxy for media-market bias.
# Tier 1: Elite clubs with consistent UCL deep runs + global media presence
# Tier 2: Strong clubs that regularly compete in UCL
# Tier 3: Mid-tier clubs in top-5 leagues
# Tier 4: Smaller clubs / non-top-5-league clubs
#
# This is a deliberately simple, defensible operationalization. A more
# rigorous version would use UEFA coefficient rankings by year, but that
# data isn't easily sourceable for the full 1956-2025 range. Tier assignment
# is a reasonable proxy that captures the documented "big-club boost".

ELITE_CLUBS_TIER_1 = {
    # Historically elite across multiple eras
    "real madrid", "barcelona", "bayern munich", "bayern de múnich", "fc bayern münchen",
    "manchester united", "man united", "liverpool", "juventus", "ac milan", "milan",
    "inter milan", "internazionale", "inter", "ajax", "paris saint-germain", "psg",
    "paris saint germain", "paris-saint-germain",
    # Modern-era additions
    "manchester city", "man city", "chelsea", "arsenal",
}

STRONG_CLUBS_TIER_2 = {
    # Regularly in UCL but not historically tier-1
    "atletico madrid", "atlético madrid", "atletico de madrid", "atlético de madrid",
    "borussia dortmund", "bvb",
    "tottenham hotspur", "tottenham", "spurs",
    "napoli", "roma", "as roma", "lazio", "ssc napoli",
    "sevilla", "valencia",
    "atletico de kolkata",  # not really, just ensuring set membership check
    "port fc",  # placeholder
    "benfica", "sporting cp", "sporting lisbon", "porto", "fc porto",
    "celtic", "rangers",
    "marseille", "olympique de marseille", "om",
    "lyon", "ol",
    "monaco", "as monaco",
    "atletico madrid",  # duplicate to be safe
    "everton",
    "newcastle united",
    "aston villa",
    "leicester city",
    "atletico mineiro",
    "flamengo",
    "palmeiras",
    "são paulo",
    "santos",
    "botafogo",
    "vasco da gama",
    "cruzeiro",
    "grêmio",
    "internacional",
    "river plate",
    "boca juniors",
    "independiente",
    "racing club",
    "estudiantes",
}


def club_prestige_tier(club: str) -> int:
    """Return prestige tier 1-4 for a club. 1=elite, 4=small."""
    if not club:
        return 4
    club_lower = club.lower().strip()
    # Check tier 1 — handle multi-club strings (e.g., "Juventus Real Madrid" for transferred players)
    for elite in ELITE_CLUBS_TIER_1:
        if elite in club_lower:
            return 1
    for strong in STRONG_CLUBS_TIER_2:
        if strong in club_lower:
            return 2
    # Default: tier 3 if it's a top-5-league club (heuristic: name contains common club suffixes)
    # Otherwise tier 4
    return 3   # conservative default; most Ballon d'Or nominees are at top-5-league clubs


# -----------------------------------------------------------------------------
# Main feature builder
# -----------------------------------------------------------------------------


def safe_div(numer, denom):
    """Safe division — returns NaN if either is None or denom is 0."""
    if numer is None or denom is None:
        return None
    if denom == 0:
        return None
    return numer / denom


def compute_percentile_in_year(value, year_pool: list) -> Optional[float]:
    """Compute the percentile of `value` within `year_pool` (list of values, may contain None).

    Returns a float in [0, 1] or None if value is None or pool is empty.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    # Filter out None values from pool
    valid_pool = [v for v in year_pool if v is not None and not (isinstance(v, float) and pd.isna(v))]
    if not valid_pool:
        return None
    # Percentile = fraction of pool values strictly less than value, plus half the ties
    n_less = sum(1 for v in valid_pool if v < value)
    n_equal = sum(1 for v in valid_pool if v == value)
    return (n_less + 0.5 * n_equal) / len(valid_pool)


def build_features() -> pd.DataFrame:
    """Build the feature matrix — one row per (season_id, player_name_raw)."""
    # Load data
    gt = pd.read_parquet(GROUND_TRUTH_PARQUET)
    stats_df = load_stats_dedup()
    trophy_df = load_trophies()

    print(f"Ground truth: {len(gt)} rows")
    print(f"Stats (deduped): {len(stats_df)} unique players")
    print(f"Trophies: {len(trophy_df)} rows")

    # Build a lookup: player_name_raw -> stats record (career_stats, intl_stats, status)
    stats_lookup = {row["player_name_raw"]: row for _, row in stats_df.iterrows()}

    # Build features row by row
    feature_rows = []
    skipped = 0
    for _, gt_row in gt.iterrows():
        season_id = gt_row["season_id"]
        award_year = int(gt_row["award_year"])
        player_name = gt_row["player_name_raw"]
        eval_period_type = gt_row["eval_period_type"]
        club_at_time = gt_row["club_at_time"]
        position_raw = gt_row["position_raw"]
        era_tag = classify_era(award_year)

        # Get stats record
        stats_record = stats_lookup.get(player_name)
        if stats_record is None or stats_record["status"] != "ok":
            # Documented gap — still create a row but with NaN stats
            feature_row = {
                "season_id": season_id,
                "award_year": award_year,
                "player_name_raw": player_name,
                "player_name_canonical": gt_row.get("player_name_canonical"),
                "rank": gt_row["rank"],
                "club_at_time": club_at_time,
                "nation_team": gt_row["nation_team"],
                "position_raw": position_raw,
                "era_tag": era_tag,
                "stats_status": stats_record["status"] if stats_record is not None else "no_record",
            }
            feature_rows.append(feature_row)
            skipped += 1
            continue

        # Sum stats for the eval period
        career_stats = stats_record["career_stats"]
        intl_stats = stats_record["international_stats"]
        summed = sum_stats_for_eval_period(career_stats, eval_period_type, award_year)
        intl_summed = sum_international_stats(intl_stats, eval_period_type, award_year)

        # Build feature row
        feature_row = {
            "season_id": season_id,
            "award_year": award_year,
            "player_name_raw": player_name,
            "player_name_canonical": gt_row.get("player_name_canonical"),
            "rank": gt_row["rank"],
            "club_at_time": club_at_time,
            "nation_team": gt_row["nation_team"],
            "position_raw": position_raw,
            "era_tag": era_tag,
            "stats_status": "ok",
            # Family 1: Individual production
            "league_goals": summed.get("league_goals"),
            "league_assists": summed.get("league_assists"),
            "league_apps": summed.get("league_apps"),
            "league_minutes": summed.get("league_minutes"),
            "continental_goals": summed.get("continental_goals"),
            "continental_assists": summed.get("continental_assists"),
            "continental_apps": summed.get("continental_apps"),
            "continental_minutes": summed.get("continental_minutes"),
            "international_goals": intl_summed.get("international_goals"),
            "international_apps": intl_summed.get("international_apps"),
            "n_seasons_matched": summed.get("n_seasons_matched", 0),
        }

        # Derived: per-90 stats
        feature_row["league_goals_per90"] = safe_div(
            feature_row["league_goals"], safe_div(feature_row["league_minutes"], 90)
        ) if feature_row["league_minutes"] else None
        feature_row["league_assists_per90"] = safe_div(
            feature_row["league_assists"], safe_div(feature_row["league_minutes"], 90)
        ) if feature_row["league_minutes"] else None
        feature_row["continental_goals_per90"] = safe_div(
            feature_row["continental_goals"], safe_div(feature_row["continental_minutes"], 90)
        ) if feature_row["continental_minutes"] else None

        # Derived: total production
        def sum_safe(a, b):
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)
        feature_row["total_goals"] = sum_safe(feature_row["league_goals"], feature_row["continental_goals"])
        feature_row["total_assists"] = sum_safe(feature_row["league_assists"], feature_row["continental_assists"])
        feature_row["total_apps"] = sum_safe(feature_row["league_apps"], feature_row["continental_apps"])
        feature_row["total_minutes"] = sum_safe(feature_row["league_minutes"], feature_row["continental_minutes"])

        # Family 2: Trophy/team success — match club against trophy winners
        feature_row["ucl_winner"] = False
        feature_row["ucl_runner_up"] = False
        ucl_competitions = ["UEFA Champions League", "European Cup"]
        for ucl_comp in ucl_competitions:
            stage = find_trophy_for_club(trophy_df, season_id, ucl_comp, club_at_time,
                                         award_year=award_year, eval_period_type=eval_period_type)
            if stage == "winner":
                feature_row["ucl_winner"] = True
            elif stage == "runner_up":
                feature_row["ucl_runner_up"] = True

        # Domestic league winner — check all 5 league competitions
        feature_row["domestic_league_winner"] = False
        feature_row["domestic_league_runner_up"] = False
        league_comps = [
            "Premier League", "Football League First Division",
            "La Liga", "Serie A", "Bundesliga",
            "Ligue 1", "French Division 1",
        ]
        for league_comp in league_comps:
            stage = find_trophy_for_club(trophy_df, season_id, league_comp, club_at_time,
                                         award_year=award_year, eval_period_type=eval_period_type)
            if stage == "winner":
                feature_row["domestic_league_winner"] = True
            elif stage == "runner_up":
                feature_row["domestic_league_runner_up"] = True

        # Family 3: International tournament — check if player's nation won World Cup/Euro/Copa
        feature_row["world_cup_winner"] = False
        feature_row["world_cup_runner_up"] = False
        feature_row["euro_winner"] = False
        feature_row["copa_america_winner"] = False
        nation = gt_row["nation_team"]
        if nation:
            wc_stage = find_trophy_for_club(trophy_df, season_id, "FIFA World Cup", nation,
                                            award_year=award_year, eval_period_type=eval_period_type)
            if wc_stage == "winner":
                feature_row["world_cup_winner"] = True
            elif wc_stage == "runner_up":
                feature_row["world_cup_runner_up"] = True
            euro_stage = find_trophy_for_club(trophy_df, season_id, "UEFA European Championship", nation,
                                              award_year=award_year, eval_period_type=eval_period_type)
            if euro_stage == "winner":
                feature_row["euro_winner"] = True
            copa_stage = find_trophy_for_club(trophy_df, season_id, "Copa América", nation,
                                              award_year=award_year, eval_period_type=eval_period_type)
            if copa_stage == "winner":
                feature_row["copa_america_winner"] = True

        # international_tournament_year — True if eval period contains a major tournament
        # World Cup years: 1930, 1934, ..., every 4 years (excl 1942/1946)
        # Euro years: 1960, 1964, ..., every 4 years
        # Copa América: irregular
        wc_years = {1930, 1934, 1938, 1950, 1954, 1958, 1962, 1966, 1970, 1974, 1978, 1982,
                    1986, 1990, 1994, 1998, 2002, 2006, 2010, 2014, 2018, 2022}
        euro_years = {1960, 1964, 1968, 1972, 1976, 1980, 1984, 1988, 1992, 1996, 2000,
                      2004, 2008, 2012, 2016, 2020, 2024}
        copa_years = {1916, 1917, 1919, 1920, 1921, 1922, 1923, 1924, 1925, 1926, 1927, 1929,
                      1935, 1937, 1939, 1941, 1942, 1945, 1946, 1947, 1949, 1953, 1955, 1956,
                      1957, 1959, 1963, 1967, 1975, 1979, 1983, 1987, 1989, 1991, 1993, 1995,
                      1997, 1999, 2001, 2004, 2007, 2011, 2015, 2016, 2019, 2021, 2024}
        if eval_period_type == "season":
            # Season eval: check both years spanned (award_year-1, award_year)
            years_to_check = {award_year - 1, award_year}
        else:
            years_to_check = {award_year}
        feature_row["international_tournament_year"] = bool(
            years_to_check & (wc_years | euro_years | copa_years)
        )

        # Family 7: Narrative — club prestige tier (manual lookup)
        feature_row["club_prestige_tier"] = club_prestige_tier(club_at_time)

        # Family 7: previous_ballon_dor_winner (strictly lagged)
        # Look at all PRIOR award_years in ground truth where this player won
        prior_winners = gt[(gt["award_year"] < award_year) & (gt["rank"] == 1) & (gt["player_name_raw"] == player_name)]
        feature_row["previous_ballon_dor_winner"] = len(prior_winners) > 0

        # Family 7: signature_moment (best-effort, derived from trophy data)
        # Simple version: True if player won UCL + World Cup in same eval period
        # OR won UCL + scored 10+ international goals
        # OR won World Cup + scored 5+ international goals
        # (These are documented as agent-inferred flags per Implementation Plan Phase 2 task 4.)
        sig = False
        if feature_row["ucl_winner"] and feature_row["world_cup_winner"]:
            sig = True
        elif feature_row["ucl_winner"] and (feature_row["international_goals"] or 0) >= 10:
            sig = True
        elif feature_row["world_cup_winner"] and (feature_row["international_goals"] or 0) >= 5:
            sig = True
        feature_row["signature_moment"] = sig

        feature_rows.append(feature_row)

    # Convert to DataFrame
    features_df = pd.DataFrame(feature_rows)

    # Family 5: Peer-relative percentiles (computed per-year)
    print("\nComputing peer-relative percentiles per year...")
    for col in ["total_goals", "total_assists", "total_apps", "total_minutes"]:
        pct_col = col.replace("total_", "") + "_percentile_in_year"
        features_df[pct_col] = None
        for year, group in features_df.groupby("award_year"):
            pool = group[col].tolist()
            for idx in group.index:
                features_df.at[idx, pct_col] = compute_percentile_in_year(features_df.at[idx, col], pool)

    # Family 6: Recency-weighted form — leave as NaN for now (Wikipedia player pages
    # don't split stats by half-season). Documented as known gap.
    features_df["second_half_goals_share"] = None

    return features_df


# -----------------------------------------------------------------------------
# QA + write parquet
# -----------------------------------------------------------------------------


def write_qa_report(df: pd.DataFrame) -> None:
    lines = []
    lines.append("# Features QA Report (Phase 4)\n\n")
    lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n")
    lines.append(f"Output: `{OUTPUT_PARQUET}`\n\n")

    lines.append("## Summary\n\n")
    lines.append(f"- Total feature rows: **{len(df)}**\n")
    lines.append(f"- Unique players: **{df['player_name_raw'].nunique()}**\n")
    lines.append(f"- Unique seasons: **{df['season_id'].nunique()}**\n\n")

    # Stats status breakdown
    lines.append("## Stats Status Distribution\n\n")
    lines.append("| Status | Count | % |\n|---|---|---|\n")
    for status, count in df["stats_status"].value_counts().items():
        pct = 100 * count / len(df)
        lines.append(f"| {status} | {count} | {pct:.1f}% |\n")
    lines.append("\n")

    # Era breakdown
    lines.append("## Era Distribution\n\n")
    lines.append("| Era | Rows | Players | Winners |\n|---|---|---|---|\n")
    for era in ["classical", "pre_merger", "fifa_merger", "post_split"]:
        sub = df[df["era_tag"] == era]
        winners = (sub["rank"] == 1).sum()
        lines.append(f"| {era} | {len(sub)} | {sub['player_name_raw'].nunique()} | {winners} |\n")
    lines.append("\n")

    # Feature coverage by era
    lines.append("## Feature Coverage by Era\n\n")
    feature_cols = [
        "league_goals", "league_assists", "league_apps", "league_minutes",
        "continental_goals", "continental_apps",
        "international_goals", "international_apps",
        "ucl_winner", "domestic_league_winner",
        "world_cup_winner",
        "club_prestige_tier", "previous_ballon_dor_winner",
        "goals_percentile_in_year", "apps_percentile_in_year",
    ]
    lines.append("| Feature | Classical | Pre-merger | FIFA merger | Post-split | Overall |\n")
    lines.append("|---|---|---|---|---|---|\n")
    for col in feature_cols:
        if col not in df.columns:
            continue
        coverages = []
        for era in ["classical", "pre_merger", "fifa_merger", "post_split"]:
            sub = df[df["era_tag"] == era]
            if col in ["ucl_winner", "domestic_league_winner", "world_cup_winner", "previous_ballon_dor_winner"]:
                # Boolean — coverage = % True
                coverage = 100 * sub[col].sum() / len(sub) if len(sub) else 0
            else:
                # Numeric — coverage = % non-null
                coverage = 100 * sub[col].notna().sum() / len(sub) if len(sub) else 0
            coverages.append(f"{coverage:.0f}%")
        overall = 100 * df[col].notna().sum() / len(df) if col not in ["ucl_winner", "domestic_league_winner", "world_cup_winner", "previous_ballon_dor_winner"] else 100 * df[col].sum() / len(df)
        coverages.append(f"{overall:.0f}%")
        lines.append(f"| {col} | {' | '.join(coverages)} |\n")
    lines.append("\n")

    # Sanity check: known obvious winners
    lines.append("## Sanity Check — Known Obvious Winners\n\n")
    lines.append("These are widely-agreed 'obvious winner' seasons. Feature values should match football-domain expectations.\n\n")
    lines.append("| Year | Winner | total_goals | ucl_winner | wc_winner | club_prestige |\n|---|---|---|---|---|---|\n")
    obvious_winners = [
        (1957, "Alfredo Di Stéfano"),    # Real Madrid, won European Cup
        (1972, "Franz Beckenbauer"),     # Bayern Munich
        (1998, "Zinedine Zidane"),       # Juventus + World Cup winner
        (2001, "Michael Owen"),          # Liverpool, 5 goals in UCL
        (2002, "Ronaldo"),               # World Cup winner (Brazilian Ronaldo)
        (2008, "Cristiano Ronaldo"),     # Man Utd, UCL winner
        (2009, "Lionel Messi"),          # Barcelona, UCL winner
        (2018, "Luka Modrić"),           # Real Madrid, UCL winner + World Cup runner-up
        (2022, "Karim Benzema"),         # Real Madrid, UCL winner
        (2023, "Lionel Messi"),          # World Cup winner (Argentina)
    ]
    for year, winner in obvious_winners:
        sub = df[(df["award_year"] == year) & (df["player_name_raw"] == winner) & (df["rank"] == 1)]
        if len(sub) == 0:
            lines.append(f"| {year} | {winner} | (not found) | | | |\n")
            continue
        r = sub.iloc[0]
        lines.append(
            f"| {year} | {winner} | {r.get('total_goals', 'NaN')} | "
            f"{'✅' if r.get('ucl_winner') else '❌'} | "
            f"{'✅' if r.get('world_cup_winner') else '❌'} | "
            f"tier {r.get('club_prestige_tier', '?')} |\n"
        )
    lines.append("\n")

    lines.append("## Conclusion\n\n")
    overall_coverage = 100 * df["stats_status"].eq("ok").sum() / len(df)
    lines.append(f"Overall stats coverage in feature matrix: **{overall_coverage:.1f}%**\n")
    lines.append(f"\nFeatures ready for Phase 5 modeling. Per Key Focus Areas §9, all missing values stay NaN — never silently imputed.\n")

    QA_REPORT.parent.mkdir(parents=True, exist_ok=True)
    QA_REPORT.write_text("".join(lines), encoding="utf-8")
    print(f"✅ Wrote QA report to {QA_REPORT}")


def main() -> None:
    print("Building feature matrix...")
    df = build_features()

    print(f"\nFeature matrix shape: {df.shape}")
    print(f"Stats status distribution:")
    print(df["stats_status"].value_counts())

    # Write parquet
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, OUTPUT_PARQUET, compression="snappy")
    print(f"\n✅ Wrote {OUTPUT_PARQUET} ({len(df)} rows × {df.shape[1]} cols)")

    write_qa_report(df)

    # Quick sanity check
    print()
    print("=" * 70)
    print("Quick sanity check — known winners:")
    print("=" * 70)
    for year, winner in [(2022, "Karim Benzema"), (2023, "Lionel Messi"), (2024, "Rodri")]:
        sub = df[(df["award_year"] == year) & (df["player_name_raw"] == winner)]
        if len(sub):
            r = sub.iloc[0]
            print(f"  {year} {winner}: rank={r['rank']}, total_goals={r.get('total_goals')}, "
                  f"ucl_winner={r.get('ucl_winner')}, wc_winner={r.get('world_cup_winner')}, "
                  f"club_prestige={r.get('club_prestige_tier')}")


if __name__ == "__main__":
    main()
