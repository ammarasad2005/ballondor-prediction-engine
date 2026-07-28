"""Entity resolution: build canonical player IDs + alias table.

Per Architecture Blueprint §4.3 + Key Focus Areas §2:
  - Canonical player ID scheme: slugified name + birth year (stable ID)
  - Explicit alias/override table for known hard cases (disambiguation
    pages, mononyms, transliteration variants)
  - Automated fuzzy-match candidate generation for low-confidence cases
  - QA report: every ground-truth row resolves to exactly one stats row

Strategy:
  Phase A: Build alias_table.yaml with manually-curated disambiguations
    for known mononyms (Rodri, Adriano, Ronaldo, etc.) and players
    whose Wikipedia slug differs from the ground-truth name.
  Phase B: For each ground-truth player, look up their stats record.
    If exact match → use it. If not, try alias table. If still no
    match, generate fuzzy-match candidates and log to review file.
  Phase C: Update ground_truth.parquet with player_name_canonical
    column (the Wikipedia page title, which serves as the canonical
    identifier across all data sources).
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
from rapidfuzz import fuzz

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
GROUND_TRUTH_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"
STATS_JSONL = PROJECT_ROOT / "data" / "raw" / "stats" / "stats_raw.jsonl"
ALIAS_TABLE_YAML = PROJECT_ROOT / "entity_resolution" / "alias_table.yaml"
OUTPUT_PARQUET = GROUND_TRUTH_PARQUET  # update in-place
QA_REPORT = PROJECT_ROOT / "data" / "processed" / "entity_resolution_qa_report.md"
REVIEW_FILE = PROJECT_ROOT / "data" / "processed" / "entity_resolution_review.txt"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

FOOTNOTE_RE = re.compile(r"\s*\[[^\]]*\]\s*")


def slugify(name: str) -> str:
    """Wikipedia-style slug: spaces to underscores, preserve diacritics."""
    if not name:
        return ""
    slug = re.sub(r"\s+", "_", name.strip())
    slug = FOOTNOTE_RE.sub("", slug)
    return slug


def strip_accents(s: str) -> str:
    """NFD decomposition + remove combining marks."""
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def normalize_for_match(s: str) -> str:
    """Aggressive normalization for fuzzy matching:
    lowercase, strip accents, collapse whitespace, strip punctuation.
    """
    if not s:
        return ""
    s = strip_accents(s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# -----------------------------------------------------------------------------
# Alias table — manually curated disambiguations
# -----------------------------------------------------------------------------

# These are players whose ground-truth name (from Wikipedia Ballon d'Or
# per-year page) does NOT directly resolve to their stats page on Wikipedia.
# Common patterns:
#   1. Mononyms that hit disambiguation pages (Rodri, Adriano, Ronaldo, etc.)
#   2. Transliteration variants (e.g., "Luka Modrić" vs "Luka Modric")
#   3. Players whose stats page uses birth-year suffix for disambiguation
#
# Each entry maps: ground_truth_name -> wikipedia_slug (the page that has
# the player's career stats table).

MANUAL_ALIASES = {
    # 2024 Ballon d'Or winner — disambiguation page lists 10 different "Rodri"s
    "Rodri": "Rodri (footballer, born 1996)",

    # Adriano — multiple Brazilians with this name
    "Adriano": "Adriano Leite Ribeiro",  # the famous Inter Milan striker

    # Ronaldo — Brazilian Ronaldo (Ronaldo Luís Nazário de Lima),
    # won Ballon d'Or in 1997 and 2002. Cristiano Ronaldo always appears
    # as "Cristiano Ronaldo" in ground truth.
    "Ronaldo": "Ronaldo (Brazilian footballer)",

    # Ronaldinho — disambiguation
    "Ronaldinho": "Ronaldinho",

    # Pelé — disambiguation
    "Pelé": "Pelé",

    # Xavi — disambiguation page (Catalan given name)
    "Xavi": "Xavi Hernández",

    # Marcelo — disambiguation page
    "Marcelo": "Marcelo Vieira",

    # Koke — disambiguation page
    "Koke": "Koke (footballer, born 1992)",  # Atlético Madrid midfielder

    # Jorginho — disambiguation page (born December 1991)
    "Jorginho": "Jorginho (footballer, born December 1991)",  # Chelsea/Arsenal Italy int'l

    # Luis Díaz — disambiguation page
    "Luis Díaz": "Luis Díaz (footballer, born 1997)",  # Liverpool Colombia winger

    # Kim Min-jae — disambiguation page
    "Kim Min-jae": "Kim Min-jae (footballer)",  # Napoli/Munich Korea defender

    # Nuno Mendes — disambiguation page
    "Nuno Mendes": "Nuno Mendes (footballer, born 2002)",  # PSG Portugal left-back
}


def build_alias_table_yaml() -> dict:
    """Build the alias_table.yaml structure with documentation."""
    return {
        "_metadata": {
            "description": "Manual alias overrides for entity resolution. Maps ground_truth player_name_raw to the Wikipedia page slug that contains their career stats.",
            "usage": "When a ground-truth player's name doesn't directly resolve to a stats page (disambiguation page, mononym, transliteration variant), look up the canonical Wikipedia slug here.",
            "maintenance": "Add entries as new disambiguation cases are discovered during Phase 3 fuzzy-match review.",
        },
        "aliases": MANUAL_ALIASES,
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def load_ground_truth() -> pd.DataFrame:
    return pd.read_parquet(GROUND_TRUTH_PARQUET)


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


def resolve_player(name: str, stats_df: pd.DataFrame, aliases: dict) -> dict:
    """Resolve a player name to their canonical Wikipedia page title.

    Returns dict with:
      - player_name_raw: input name
      - resolved_slug: the Wikipedia slug used for fetching
      - wiki_page_title: the canonical page title (from stats record)
      - resolution_method: "exact" | "alias" | "fuzzy" | "failed"
      - status: from the stats record (ok / no_career_table / etc.)
      - fuzzy_score: confidence if fuzzy match was used
    """
    # 1. Try exact match — BUT only accept if status is "ok" (not disambiguation page)
    if name in stats_df["player_name_raw"].values:
        row = stats_df[stats_df["player_name_raw"] == name].iloc[0]
        # If the exact match has status=ok, use it directly
        if row["status"] == "ok":
            return {
                "player_name_raw": name,
                "resolved_slug": row.get("player_slug", slugify(name)),
                "wiki_page_title": row.get("wiki_page_title", name),
                "resolution_method": "exact",
                "status": row["status"],
                "fuzzy_score": None,
            }
        # Otherwise fall through to alias / fuzzy matching — the exact-match
        # record is a disambiguation page or bio-only page, not the actual
        # player's stats page.

    # 2. Try alias table
    if name in aliases:
        alias_slug = aliases[name]
        # Look up the alias in stats_df by player_slug or wiki_page_title
        match = stats_df[
            (stats_df["player_slug"] == alias_slug) |
            (stats_df["wiki_page_title"] == alias_slug)
        ]
        if len(match):
            row = match.iloc[0]
            return {
                "player_name_raw": name,
                "resolved_slug": row.get("player_slug", alias_slug),
                "wiki_page_title": row.get("wiki_page_title", alias_slug),
                "resolution_method": "alias",
                "status": row["status"],
                "fuzzy_score": None,
            }
        # Alias points to a slug we haven't fetched yet — return alias as
        # the resolved slug with status=needs_refetch. The fetcher can pick
        # this up and fetch the alias page on the next run.
        return {
            "player_name_raw": name,
            "resolved_slug": alias_slug,
            "wiki_page_title": alias_slug,
            "resolution_method": "alias_needs_refetch",
            "status": "needs_refetch",
            "fuzzy_score": None,
        }

    # 3. Try fuzzy match against all stats player_name_raw values where status=ok
    name_norm = normalize_for_match(name)
    best_score = 0
    best_match = None
    # Only fuzzy-match against ok-status records (skip disambiguation pages)
    ok_stats = stats_df[stats_df["status"] == "ok"]
    for stats_name in ok_stats["player_name_raw"].unique():
        stats_norm = normalize_for_match(stats_name)
        if not stats_norm:
            continue
        # Use token_sort_ratio to handle word-order differences
        score = fuzz.token_sort_ratio(name_norm, stats_norm)
        if score > best_score:
            best_score = score
            best_match = stats_name

    if best_score >= 90 and best_match:
        # High-confidence fuzzy match — accept it
        row = ok_stats[ok_stats["player_name_raw"] == best_match].iloc[0]
        return {
            "player_name_raw": name,
            "resolved_slug": row.get("player_slug", slugify(best_match)),
            "wiki_page_title": row.get("wiki_page_title", best_match),
            "resolution_method": "fuzzy",
            "status": row["status"],
            "fuzzy_score": best_score,
        }

    # 4. Failed — log for review (return the original exact-match record
    # if it exists, so we still have the disambiguation-page info)
    if name in stats_df["player_name_raw"].values:
        row = stats_df[stats_df["player_name_raw"] == name].iloc[0]
        return {
            "player_name_raw": name,
            "resolved_slug": row.get("player_slug", slugify(name)),
            "wiki_page_title": row.get("wiki_page_title", name),
            "resolution_method": "failed_alias_needed",
            "status": row["status"],
            "fuzzy_score": best_score,
            "fuzzy_candidate": best_match,
        }

    return {
        "player_name_raw": name,
        "resolved_slug": None,
        "wiki_page_title": None,
        "resolution_method": "failed",
        "status": "unresolved",
        "fuzzy_score": best_score if best_match else 0,
        "fuzzy_candidate": best_match,
    }


def main() -> None:
    print("Loading ground truth + stats...")
    gt = load_ground_truth()
    stats_df = load_stats_dedup()
    print(f"  Ground truth: {len(gt)} rows, {gt['player_name_raw'].nunique()} unique players")
    print(f"  Stats (deduped): {len(stats_df)} unique players")

    # Write alias_table.yaml
    alias_data = build_alias_table_yaml()
    with open(ALIAS_TABLE_YAML, "w", encoding="utf-8") as f:
        yaml.dump(alias_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"\n✅ Wrote {ALIAS_TABLE_YAML}")

    # Resolve each unique player
    aliases = MANUAL_ALIASES.copy()
    unique_players = gt["player_name_raw"].unique().tolist()
    print(f"\nResolving {len(unique_players)} unique players...")

    resolutions = []
    for name in unique_players:
        r = resolve_player(name, stats_df, aliases)
        resolutions.append(r)

    res_df = pd.DataFrame(resolutions)

    # Summary
    print()
    print("=" * 70)
    print("Resolution summary:")
    print("=" * 70)
    method_counts = res_df["resolution_method"].value_counts()
    print(method_counts)
    print()
    status_counts = res_df["status"].value_counts()
    print("Status of resolved players:")
    print(status_counts)

    # Write review file for low-confidence fuzzy matches + failures
    review_lines = []
    review_lines.append("# Entity Resolution Review File\n\n")
    review_lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n")
    review_lines.append(f"Total unique players: {len(res_df)}\n\n")
    review_lines.append("## Players needing manual review\n\n")
    review_lines.append("These are players where:\n")
    review_lines.append("- resolution_method = 'fuzzy' with score < 95 (low confidence)\n")
    review_lines.append("- resolution_method = 'failed' (no match found)\n")
    review_lines.append("- resolution_method = 'alias_needs_refetch' (alias points to a page not yet cached)\n\n")
    review_lines.append("| player_name_raw | method | status | fuzzy_score | fuzzy_candidate | resolved_slug |\n")
    review_lines.append("|---|---|---|---|---|---|\n")
    needs_review = res_df[
        (res_df["resolution_method"] == "failed") |
        ((res_df["resolution_method"] == "fuzzy") & (res_df["fuzzy_score"] < 95)) |
        (res_df["resolution_method"] == "alias_needs_refetch")
    ].sort_values("resolution_method")
    for _, r in needs_review.iterrows():
        review_lines.append(
            f"| {r['player_name_raw']} | {r['resolution_method']} | {r['status']} | "
            f"{r.get('fuzzy_score', '')} | {r.get('fuzzy_candidate', '')} | {r.get('resolved_slug', '')} |\n"
        )
    review_lines.append(f"\nTotal needing review: {len(needs_review)}\n")
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_FILE.write_text("".join(review_lines), encoding="utf-8")
    print(f"\n✅ Wrote review file to {REVIEW_FILE}")
    print(f"   Players needing review: {len(needs_review)}")

    # Update ground_truth.parquet with player_name_canonical
    # Build a lookup: player_name_raw -> wiki_page_title (or player_name_raw if unresolved)
    name_to_canonical = {}
    for _, r in res_df.iterrows():
        canonical = r.get("wiki_page_title") or r["player_name_raw"]
        name_to_canonical[r["player_name_raw"]] = canonical

    gt["player_name_canonical"] = gt["player_name_raw"].map(name_to_canonical)

    # Write back to parquet
    table = gt_to_pyarrow_table(gt)
    import pyarrow.parquet as pq
    pq.write_table(table, OUTPUT_PARQUET, compression="snappy")
    print(f"\n✅ Updated {OUTPUT_PARQUET} with player_name_canonical column")
    print(f"   Total rows: {len(gt)}")
    print(f"   Rows with canonical name set: {gt['player_name_canonical'].notna().sum()}")

    # QA report
    write_qa_report(gt, res_df, needs_review)


def gt_to_pyarrow_table(df: pd.DataFrame):
    import pyarrow as pa
    return pa.Table.from_pandas(df, preserve_index=False)


def write_qa_report(gt: pd.DataFrame, res_df: pd.DataFrame, needs_review: pd.DataFrame) -> None:
    lines = []
    lines.append("# Entity Resolution QA Report (Phase 3)\n\n")
    lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n\n")

    lines.append("## Summary\n\n")
    lines.append(f"- Total unique players in ground truth: **{len(res_df)}**\n")
    lines.append(f"- Resolved via exact name match: **{(res_df['resolution_method'] == 'exact').sum()}**\n")
    lines.append(f"- Resolved via alias table: **{(res_df['resolution_method'] == 'alias').sum()}**\n")
    lines.append(f"- Resolved via fuzzy match (score ≥ 90): **{(res_df['resolution_method'] == 'fuzzy').sum()}**\n")
    lines.append(f"- Failed (no match found): **{(res_df['resolution_method'] == 'failed').sum()}**\n")
    lines.append(f"- Alias needs refetch (page not yet cached): **{(res_df['resolution_method'] == 'alias_needs_refetch').sum()}**\n\n")

    lines.append("## Resolution Method Distribution\n\n")
    lines.append("| Method | Count | Status |\n|---|---|---|\n")
    for method, count in res_df["resolution_method"].value_counts().items():
        sub = res_df[res_df["resolution_method"] == method]
        statuses = sub["status"].value_counts().to_dict()
        status_str = ", ".join(f"{s}={c}" for s, c in statuses.items())
        lines.append(f"| {method} | {count} | {status_str} |\n")
    lines.append("\n")

    lines.append("## Players Needing Manual Review\n\n")
    lines.append(f"Total: **{len(needs_review)}**\n\n")
    if len(needs_review):
        lines.append("See `data/processed/entity_resolution_review.txt` for the full list.\n\n")
        lines.append("### Failed resolutions (no match found)\n\n")
        failed = needs_review[needs_review["resolution_method"] == "failed"]
        if len(failed):
            lines.append("| player_name_raw | best fuzzy_score | fuzzy_candidate |\n|---|---|---|\n")
            for _, r in failed.head(20).iterrows():
                lines.append(f"| {r['player_name_raw']} | {r.get('fuzzy_score', 0)} | {r.get('fuzzy_candidate', '')} |\n")
            lines.append("\n")

    # Coverage check — per Implementation Plan Phase 3 exit criterion
    lines.append("## Coverage Check (Phase 3 Exit Criterion)\n\n")
    lines.append("Per Implementation Plan Phase 3:\n")
    lines.append("> Zero unresolved ground-truth rows remain silently unjoined — every row either\n")
    lines.append("> successfully joins or is explicitly logged as a documented gap with a stated reason.\n\n")
    unresolved = (res_df["resolution_method"] == "failed").sum()
    needs_refetch = (res_df["resolution_method"] == "alias_needs_refetch").sum()
    no_career = (res_df["status"] == "no_career_table").sum()
    ok_count = (res_df["status"] == "ok").sum()
    lines.append(f"- ✅ Resolved with stats (status=ok): **{ok_count}** players\n")
    lines.append(f"- ⚠️ Resolved but no career table on Wikipedia (status=no_career_table): **{no_career}** players (documented gap)\n")
    lines.append(f"- ⚠️ Alias points to page not yet cached (alias_needs_refetch): **{needs_refetch}** players\n")
    lines.append(f"- ❌ Failed (no match found): **{unresolved}** players\n\n")
    if unresolved == 0:
        lines.append("**Phase 3 exit criterion: MET** — zero unresolved ground-truth rows.\n")
    else:
        lines.append(f"**Phase 3 exit criterion: NOT MET** — {unresolved} unresolved rows need manual alias additions.\n")

    QA_REPORT.parent.mkdir(parents=True, exist_ok=True)
    QA_REPORT.write_text("".join(lines), encoding="utf-8")
    print(f"\n✅ Wrote QA report to {QA_REPORT}")


if __name__ == "__main__":
    main()
