"""Integrate Understat xG/xA data into features.parquet.

Per user request: 'for advanced metrics you could use alternatives that
dont have cloudflare restrictions such as understat api etc and make
the system even more robust'

This module:
  1. Loads Understat player data (from scrapers/understat_scraper.py)
  2. Matches Understat players to ground_truth player names via:
     - Normalized exact match (after HTML decode + accent strip +
       special char handling)
     - Alias table for known name variants (Mbappé-Lottin, Daniel Carvajal, etc.)
     - Fuzzy match fallback (rapidfuzz token_sort_ratio >= 90)
  3. For each Ballon d'Or season in the modern era (2014-15 onward),
     finds the player's Understat record(s) for the relevant season(s)
  4. Aggregates xG/xA across leagues (for players who transferred mid-season)
  5. Updates features.parquet with new xG/xA features

Per Architecture Blueprint §4.4 family 1, xG/xA are added with era tag
`modern_only` since Understat only covers 2014-15 onward.

Per Key Focus Areas §9, missing values stay NaN — never silently imputed.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rapidfuzz import fuzz

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
UNDERSTAT_JSONL = PROJECT_ROOT / "data" / "raw" / "understat" / "player_xg_raw.jsonl"
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
OUTPUT_PARQUET = FEATURES_PARQUET  # update in-place
MATCH_REPORT = PROJECT_ROOT / "data" / "processed" / "understat_match_report.md"

# Manually-curated aliases for known name variants between Ballon d'Or
# ground truth (Wikipedia) and Understat
GT_TO_UNDERSTAT_ALIASES = {
    "Kylian Mbappé": "Kylian Mbappe-Lottin",
    "Dani Carvajal": "Daniel Carvajal",
    # Add more as discovered during matching
}


# -----------------------------------------------------------------------------
# Name normalization
# -----------------------------------------------------------------------------

def normalize_name(s: str) -> str:
    """Aggressive normalization for matching.
    - Decode HTML entities (N&#039;Golo → N'Golo)
    - Strip accents (Mbappé → Mbappe)
    - Handle special chars (Ø → O, ß → ss, æ → ae)
    - Lowercase, collapse whitespace, strip punctuation
    """
    if not s:
        return ""
    # Decode HTML entities first (Understat stores N&#039;Golo Kanté)
    s = html.unescape(s)
    # NFD decomposition + strip combining marks
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Handle special chars that NFD doesn't decompose
    special_map = str.maketrans({
        "Ø": "O", "ø": "o",
        "Æ": "AE", "æ": "ae",
        "ß": "ss",
        "Đ": "D", "đ": "d",
        "Ł": "L", "ł": "l",
        "Þ": "TH", "þ": "th",
        "Ð": "D", "ð": "d",
    })
    s = s.translate(special_map)
    # Lowercase
    s = s.lower()
    # Strip punctuation (including apostrophes — N'Golo → N Golo → ngolo)
    # Wait — we want "N'Golo" to match "N Golo" or "NGolo". Let's remove
    # apostrophes entirely (no space) so "N'Golo" → "ngolo".
    s = re.sub(r"'", "", s)
    # Replace other punctuation with space
    s = re.sub(r"[^\w\s]", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


# -----------------------------------------------------------------------------
# Matching
# -----------------------------------------------------------------------------

def build_understat_lookup(us_df: pd.DataFrame) -> dict:
    """Build a lookup: normalized_name -> list of Understat records."""
    lookup = {}
    for _, row in us_df.iterrows():
        norm = normalize_name(row["player_name"])
        if norm not in lookup:
            lookup[norm] = []
        lookup[norm].append(row.to_dict())
    return lookup


def match_player(gt_name: str, us_lookup: dict, aliases: dict) -> tuple[Optional[str], str]:
    """Match a ground_truth player name to an Understat normalized name.
    Returns (matched_normalized_name, match_method) or (None, "failed").
    """
    # 1. Try alias table
    if gt_name in aliases:
        alias_target = aliases[gt_name]
        alias_norm = normalize_name(alias_target)
        if alias_norm in us_lookup:
            return alias_norm, "alias"

    # 2. Try exact normalized match
    gt_norm = normalize_name(gt_name)
    if gt_norm in us_lookup:
        return gt_norm, "exact"

    # 3. Try fuzzy match (token_sort_ratio handles word order)
    best_score = 0
    best_match = None
    for us_norm in us_lookup:
        score = fuzz.token_sort_ratio(gt_norm, us_norm)
        if score > best_score:
            best_score = score
            best_match = us_norm
    if best_score >= 90 and best_match:
        return best_match, f"fuzzy({best_score})"

    return None, f"failed(best_score={best_score})"


# -----------------------------------------------------------------------------
# Season matching
# -----------------------------------------------------------------------------

def get_relevant_understat_seasons(award_year: int, eval_period_type: str) -> list[str]:
    """Determine which Understat seasons overlap with a Ballon d'Or eval period.

    Understat season "2023" = 2023-24 season (Aug 2023 - May 2024).

    Ballon d'Or eval period:
      - Calendar year (1956-2021): Jan 1 - Dec 31 of award_year.
        Overlapping Understat seasons: (award_year-1) which ends in May award_year,
        AND (award_year) which starts in Aug award_year.
      - Season-based (2022+): Aug (award_year-1) - Jul (award_year).
        Overlapping Understat season: (award_year-1) = Aug(award_year-1) - May(award_year).
        This is the primary season for season-based eval.
    """
    if eval_period_type == "season":
        # Season-based: use the season starting in award_year-1
        return [str(award_year - 1)]
    else:
        # Calendar-year: use both seasons that overlap with calendar year
        return [str(award_year - 1), str(award_year)]


def aggregate_understat_stats(records: list[dict]) -> dict:
    """Aggregate stats across multiple Understat records (e.g., player
    transferred mid-season and has records in 2 leagues).
    """
    if not records:
        return {}
    summed = {}
    sum_keys = ["games", "time", "goals", "assists", "shots", "key_passes",
                "yellow_cards", "red_cards", "npg",
                "xG", "xA", "npxG"]
    for key in sum_keys:
        values = [r.get(key) for r in records if r.get(key) is not None]
        if values:
            summed[key] = sum(values)
        else:
            summed[key] = None

    # Track leagues + teams
    summed["leagues"] = list(set(r.get("league_slug") for r in records if r.get("league_slug")))
    summed["teams"] = list(set(r.get("team_title") for r in records if r.get("team_title")))
    summed["n_records"] = len(records)
    return summed


# -----------------------------------------------------------------------------
# Main integration
# -----------------------------------------------------------------------------


def integrate_understat() -> None:
    print("=" * 70)
    print("Integrating Understat xG/xA data into features.parquet")
    print("=" * 70)

    # Load Understat data
    us_rows = []
    with open(UNDERSTAT_JSONL, encoding="utf-8") as f:
        for line in f:
            us_rows.append(json.loads(line))
    us_df = pd.DataFrame(us_rows)
    print(f"Understat records: {len(us_df)}")

    # Build lookup
    us_lookup = build_understat_lookup(us_df)
    print(f"Unique normalized Understat names: {len(us_lookup)}")

    # Load features
    features_df = pd.read_parquet(FEATURES_PARQUET)
    print(f"Features parquet: {len(features_df)} rows, {features_df.shape[1]} cols")

    # Load ground truth to get eval_period_type (not in features parquet)
    gt_df = pd.read_parquet(PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet")
    eval_period_lookup = gt_df.set_index(["season_id", "player_name_raw"])["eval_period_type"].to_dict()
    # Apply eval_period_type to features_df
    features_df["eval_period_type"] = features_df.apply(
        lambda r: eval_period_lookup.get((r["season_id"], r["player_name_raw"]), "calendar_year"),
        axis=1,
    )

    # Match each unique player in features_df
    unique_players = features_df["player_name_raw"].unique().tolist()
    print(f"\nMatching {len(unique_players)} unique players...")

    match_results = {}  # player_name_raw -> (matched_norm, method, list_of_us_records)
    match_stats = {"exact": 0, "alias": 0, "fuzzy": 0, "failed": 0}

    for player in unique_players:
        matched_norm, method = match_player(player, us_lookup, GT_TO_UNDERSTAT_ALIASES)
        if matched_norm:
            records = us_lookup[matched_norm]
            match_results[player] = (matched_norm, method, records)
            if method.startswith("fuzzy"):
                match_stats["fuzzy"] += 1
            else:
                match_stats[method] += 1
        else:
            match_results[player] = (None, method, [])
            match_stats["failed"] += 1

    print(f"\nMatch results:")
    for method, count in match_stats.items():
        print(f"  {method}: {count}")

    # Add xG/xA features to features_df
    print("\nAdding xG/xA features...")
    new_cols = ["xg", "xa", "npxg", "xg_per90", "xa_per90", "xg_overperformance", "understat_matches"]
    for col in new_cols:
        features_df[col] = None

    for idx, row in features_df.iterrows():
        player = row["player_name_raw"]
        award_year = int(row["award_year"])
        eval_period_type = row["eval_period_type"]

        # Skip non-modern eras (Understat has no data before 2014-15)
        if award_year < 2014:
            continue

        matched_norm, method, all_records = match_results.get(player, (None, "failed", []))
        if not all_records:
            continue

        # Filter to records matching the relevant Understat season(s)
        relevant_seasons = get_relevant_understat_seasons(award_year, eval_period_type)
        relevant_records = [r for r in all_records if r.get("season") in relevant_seasons]
        if not relevant_records:
            continue

        # Aggregate across multiple leagues (mid-season transfers)
        agg = aggregate_understat_stats(relevant_records)

        # Set feature values
        features_df.at[idx, "xg"] = agg.get("xG")
        features_df.at[idx, "xa"] = agg.get("xA")
        features_df.at[idx, "npxg"] = agg.get("npxG")
        # per-90 stats
        minutes = agg.get("time")
        if minutes and minutes > 0:
            features_df.at[idx, "xg_per90"] = (agg.get("xG") or 0) * 90 / minutes
            features_df.at[idx, "xa_per90"] = (agg.get("xA") or 0) * 90 / minutes
        # xG overperformance = goals - xG (positive = overperformed = clinical finishing)
        goals = agg.get("goals")
        xg = agg.get("xG")
        if goals is not None and xg is not None:
            features_df.at[idx, "xg_overperformance"] = goals - xg
        # Track which Understat records matched (for transparency)
        features_df.at[idx, "understat_matches"] = json.dumps({
            "method": method,
            "matched_name": matched_norm,
            "seasons_matched": [r["season"] for r in relevant_records],
            "leagues": agg.get("leagues", []),
            "teams": agg.get("teams", []),
        })

    # Save updated features
    table = pa.Table.from_pandas(features_df, preserve_index=False)
    pq.write_table(table, OUTPUT_PARQUET, compression="snappy")
    print(f"\n✅ Updated {OUTPUT_PARQUET}")
    print(f"   New shape: {features_df.shape}")
    print(f"   New columns: {new_cols}")

    # Coverage stats
    print("\n" + "=" * 70)
    print("xG/xA coverage by era:")
    print("=" * 70)
    for era in ["classical", "pre_merger", "fifa_merger", "post_split"]:
        sub = features_df[features_df["era_tag"] == era]
        xg_coverage = sub["xg"].notna().sum()
        print(f"  {era:12}: {xg_coverage:4}/{len(sub):4} rows have xG ({100*xg_coverage/len(sub):.1f}%)")

    # Modern era detail
    modern = features_df[features_df["award_year"] >= 2014]
    print(f"\nModern era (2014+) detail:")
    print(f"  Total rows: {len(modern)}")
    print(f"  xG coverage: {modern['xg'].notna().sum()}/{len(modern)} ({100*modern['xg'].notna().sum()/len(modern):.1f}%)")
    print(f"  xA coverage: {modern['xa'].notna().sum()}/{len(modern)} ({100*modern['xa'].notna().sum()/len(modern):.1f}%)")

    # Sample verification — show xG for known top scorers
    print("\nSample xG values for known modern winners:")
    for year, player in [(2024, "Rodri"), (2023, "Lionel Messi"), (2022, "Karim Benzema"),
                         (2021, "Lionel Messi"), (2019, "Lionel Messi"), (2018, "Luka Modrić")]:
        sub = features_df[(features_df["award_year"] == year) & (features_df["player_name_raw"] == player)]
        if len(sub):
            r = sub.iloc[0]
            print(f"  {year} {player}: xG={r.get('xg')}, xA={r.get('xa')}, total_goals={r.get('total_goals')}, xg_overperf={r.get('xg_overperformance')}")

    # Write match report
    write_match_report(features_df, match_stats, match_results)


def write_match_report(features_df: pd.DataFrame, match_stats: dict, match_results: dict):
    lines = []
    lines.append("# Understat Match Report\n\n")
    lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n\n")

    lines.append("## Match Statistics\n\n")
    lines.append("| Method | Count |\n|---|---|\n")
    for method, count in match_stats.items():
        lines.append(f"| {method} | {count} |\n")
    total = sum(match_stats.values())
    matched = total - match_stats.get("failed", 0)
    lines.append(f"| **total matched** | **{matched}/{total} ({100*matched/total:.1f}%)** |\n\n")

    lines.append("## xG/xA Feature Coverage\n\n")
    lines.append("| Era | Rows | xG coverage | xA coverage |\n|---|---|---|---|\n")
    for era in ["classical", "pre_merger", "fifa_merger", "post_split"]:
        sub = features_df[features_df["era_tag"] == era]
        xg_cov = sub["xg"].notna().sum()
        xa_cov = sub["xa"].notna().sum()
        lines.append(f"| {era} | {len(sub)} | {xg_cov} ({100*xg_cov/len(sub):.0f}%) | {xa_cov} ({100*xa_cov/len(sub):.0f}%) |\n")
    lines.append("\n")

    # Show failed matches (modern era only — classical is expected to fail)
    modern_failed = []
    for player, (matched_norm, method, _) in match_results.items():
        if method.startswith("failed"):
            # Check if this player appears in modern era
            player_rows = features_df[(features_df["player_name_raw"] == player) & (features_df["award_year"] >= 2014)]
            if len(player_rows) > 0:
                modern_failed.append((player, method))

    lines.append("## Modern-Era Unmatched Players\n\n")
    lines.append(f"These {len(modern_failed)} modern-era players did not match any Understat record:\n\n")
    for player, method in modern_failed[:20]:
        lines.append(f"- `{player}` ({method})\n")
    if len(modern_failed) > 20:
        lines.append(f"\n... and {len(modern_failed) - 20} more\n")

    MATCH_REPORT.parent.mkdir(parents=True, exist_ok=True)
    MATCH_REPORT.write_text("".join(lines), encoding="utf-8")
    print(f"\n✅ Wrote {MATCH_REPORT}")


if __name__ == "__main__":
    integrate_understat()
