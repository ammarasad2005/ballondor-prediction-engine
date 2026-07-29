"""Combined fix script — applies all 3 audit bug fixes at once.

Fix 1 (Finding 3): Populate assists + minutes from Understat
Fix 2 (Finding 8): Remove collinear signature_moment from feature lists
Fix 3 (Finding 10): Add has_stats_data + data_completeness_score flags
"""
import html, json, re, unicodedata
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rapidfuzz import fuzz

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
GT_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"
UNDERSTAT_JSONL = PROJECT_ROOT / "data" / "raw" / "understat" / "player_xg_raw.jsonl"

GT_TO_UNDERSTAT_ALIASES = {
    "Kylian Mbappé": "Kylian Mbappe-Lottin",
    "Dani Carvajal": "Daniel Carvajal",
}

def normalize_name(s):
    if not s: return ""
    s = html.unescape(s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.translate(str.maketrans({"Ø": "O", "ø": "o", "Æ": "AE", "æ": "ae", "ß": "ss"}))
    s = s.lower()
    s = re.sub(r"'", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def get_seasons(award_year, eval_period_type):
    if eval_period_type == "season": return [str(award_year - 1)]
    return [str(award_year - 1), str(award_year)]

def aggregate(records):
    if not records: return {}
    summed = {}
    for key in ["assists", "time", "goals", "xG", "xA"]:
        values = [r.get(key) for r in records if r.get(key) is not None]
        summed[key] = sum(values) if values else None
    return summed

def percentile(value, pool):
    if value is None or (isinstance(value, float) and pd.isna(value)): return None
    valid = [v for v in pool if v is not None and not (isinstance(v, float) and pd.isna(v))]
    if not valid: return None
    return (sum(1 for v in valid if v < value) + 0.5 * sum(1 for v in valid if v == value)) / len(valid)

def sum_safe(a, b):
    if pd.isna(a) and pd.isna(b): return None
    return (0 if pd.isna(a) else a) + (0 if pd.isna(b) else b)

def main():
    print("=" * 70)
    print("Applying 3 audit bug fixes")
    print("=" * 70)

    # Load data
    df = pd.read_parquet(FEATURES_PARQUET)
    gt_df = pd.read_parquet(GT_PARQUET)
    eval_lookup = gt_df.set_index(["season_id", "player_name_raw"])["eval_period_type"].to_dict()
    df["eval_period_type"] = df.apply(
        lambda r: eval_lookup.get((r["season_id"], r["player_name_raw"]), "calendar_year"), axis=1)

    # Load Understat
    us_rows = [json.loads(l) for l in open(UNDERSTAT_JSONL)]
    us_df = pd.DataFrame(us_rows)
    us_lookup = {}
    for _, row in us_df.iterrows():
        norm = normalize_name(row["player_name"])
        us_lookup.setdefault(norm, []).append(row.to_dict())

    # FIX 1: Populate assists + minutes from Understat
    print("\nFix 1: Populating assists + minutes from Understat...")
    updated = 0
    for idx, row in df.iterrows():
        if int(row["award_year"]) < 2014: continue
        player = row["player_name_raw"]
        matched = None
        if player in GT_TO_UNDERSTAT_ALIASES:
            alias_norm = normalize_name(GT_TO_UNDERSTAT_ALIASES[player])
            if alias_norm in us_lookup: matched = alias_norm
        if not matched:
            gt_norm = normalize_name(player)
            if gt_norm in us_lookup: matched = gt_norm
        if not matched:
            gt_norm = normalize_name(player)
            best_score, best_match = 0, None
            for us_norm in us_lookup:
                score = fuzz.token_sort_ratio(gt_norm, us_norm)
                if score > best_score: best_score, best_match = score, us_norm
            if best_score >= 90: matched = best_match
        if not matched: continue

        records = us_lookup[matched]
        seasons = get_seasons(int(row["award_year"]), row["eval_period_type"])
        relevant = [r for r in records if r.get("season") in seasons]
        if not relevant: continue
        agg = aggregate(relevant)
        if pd.isna(df.at[idx, "league_assists"]) and agg.get("assists") is not None:
            df.at[idx, "league_assists"] = agg["assists"]; updated += 1
        if pd.isna(df.at[idx, "league_minutes"]) and agg.get("time") is not None:
            df.at[idx, "league_minutes"] = agg["time"]; updated += 1
    print(f"  Updated {updated} cells")

    # Recompute derived features
    print("  Recomputing derived features...")
    df["total_assists"] = df.apply(lambda r: sum_safe(r.get("league_assists"), r.get("continental_assists")), axis=1)
    df["total_minutes"] = df.apply(lambda r: sum_safe(r.get("league_minutes"), r.get("continental_minutes")), axis=1)
    df["league_assists_per90"] = df.apply(
        lambda r: r["league_assists"] * 90 / r["league_minutes"] if pd.notna(r.get("league_assists")) and pd.notna(r.get("league_minutes")) and r["league_minutes"] > 0 else None, axis=1)
    # Recompute percentiles per year
    for year, group in df.groupby("award_year"):
        ap, mp = group["total_assists"].tolist(), group["total_minutes"].tolist()
        for idx in group.index:
            df.at[idx, "assists_percentile_in_year"] = percentile(df.at[idx, "total_assists"], ap)
            df.at[idx, "minutes_percentile_in_year"] = percentile(df.at[idx, "total_minutes"], mp)

    # FIX 3: Add has_stats_data + data_completeness_score
    print("\nFix 3: Adding has_stats_data + data_completeness_score...")
    df["has_stats_data"] = (df["stats_status"] == "ok").astype(int)
    key_stats = ["total_goals", "total_assists", "total_apps", "total_minutes", "continental_goals", "international_goals"]
    df["data_completeness_score"] = df[key_stats].notna().sum(axis=1) / len(key_stats)
    print(f"  has_stats_data: {df['has_stats_data'].sum()}/{len(df)} True")

    # Save features.parquet
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, FEATURES_PARQUET, compression="snappy")
    print(f"\n✅ Updated {FEATURES_PARQUET} ({df.shape[1]} cols)")

    # FIX 2: Remove signature_moment + add has_stats_data/data_completeness_score to feature lists
    print("\nFix 2: Updating feature lists (remove signature_moment, add new features)...")
    files = [
        "inference/predict_season.py",
        "models/tier_b_linear_ranker.py",
        "models/tier_c_gbm_ranker.py",
        "validation/run_validation.py",
        "validation/xg_xa_comparison.py",
    ]
    for filepath in files:
        path = PROJECT_ROOT / filepath
        content = path.read_text()
        # Remove signature_moment from FEATURES lists only (not from TIER_A_WEIGHTS or expected_signs dicts)
        # The FEATURES lists have: "signature_moment", (with quotes and comma)
        # The dicts have: "signature_moment": (with quotes and colon)
        # Only remove the list version
        content = content.replace('"signature_moment", ', '')
        content = content.replace('"signature_moment"\n', '')

        # Add has_stats_data + data_completeness_score to FEATURES lists
        # Only if not already there
        if "has_stats_data" not in content:
            # Find the FEATURES list and add after previous_ballon_dor_winner
            # Be careful not to modify dict entries (which have : not ,)
            content = content.replace(
                '"previous_ballon_dor_winner",\n',
                '"previous_ballon_dor_winner",\n    "has_stats_data", "data_completeness_score",\n'
            )
            # Also handle inline format
            content = content.replace(
                '"previous_ballon_dor_winner", "total_goals"',
                '"previous_ballon_dor_winner", "has_stats_data", "data_completeness_score",\n    "total_goals"'
            )

        path.write_text(content)

        # Verify syntax
        try:
            compile(content, str(path), "exec")
            print(f"  ✅ {filepath}")
        except SyntaxError as e:
            print(f"  ❌ {filepath} at line {e.lineno}: {e.msg}")

    # Also fix TIER_A_WEIGHTS in run_validation.py (remove signature_moment weight)
    path = PROJECT_ROOT / "validation/run_validation.py"
    content = path.read_text()
    content = content.replace('"signature_moment": 1.5,\n', '')
    # Add has_stats_data weight
    if '"has_stats_data":' not in content:
        content = content.replace(
            '"previous_ballon_dor_winner": 1.0,\n',
            '"previous_ballon_dor_winner": 1.0,\n    "has_stats_data": 0.5,\n    "data_completeness_score": 0.3,\n'
        )
    path.write_text(content)
    try:
        compile(content, str(path), "exec")
        print(f"  ✅ validation/run_validation.py (TIER_A_WEIGHTS fixed)")
    except SyntaxError as e:
        print(f"  ❌ validation/run_validation.py TIER_A_WEIGHTS at line {e.lineno}: {e.msg}")

    # Fix expected_signs in tier_b
    path = PROJECT_ROOT / "models/tier_b_linear_ranker.py"
    content = path.read_text()
    content = content.replace('"signature_moment": "+",\n', '')
    if '"has_stats_data":' not in content:
        content = content.replace(
            '"previous_ballon_dor_winner": "+",\n',
            '"previous_ballon_dor_winner": "+",\n        "has_stats_data": "+",\n        "data_completeness_score": "+",\n'
        )
    path.write_text(content)
    try:
        compile(content, str(path), "exec")
        print(f"  ✅ models/tier_b_linear_ranker.py (expected_signs fixed)")
    except SyntaxError as e:
        print(f"  ❌ models/tier_b_linear_ranker.py expected_signs at line {e.lineno}: {e.msg}")

    # Print summary
    print("\n" + "=" * 70)
    print("All 3 fixes applied. Summary:")
    print("=" * 70)
    print(f"  Fix 1: league_assists now {df['league_assists'].notna().sum()}/{len(df)} non-null")
    print(f"  Fix 2: signature_moment removed from feature lists")
    print(f"  Fix 3: has_stats_data + data_completeness_score added")

    # Verify feature list
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "inference"))
    if "predict_season" in sys.modules: del sys.modules["predict_season"]
    from predict_season import FEATURES
    print(f"\n  Current FEATURES list ({len(FEATURES)} features):")
    for f in FEATURES:
        print(f"    {f}")

if __name__ == "__main__":
    main()
