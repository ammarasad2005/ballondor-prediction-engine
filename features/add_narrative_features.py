"""P1-D: Career narrative arc features.

Per implementation plan: ~15% of missed winners have narrative components
(Messi 2021 Copa América redemption, Dembélé 2025 breakout).

These features capture temporal patterns in a player's career:
  - years_in_top_5: how many prior years finished top-5 ("overdue" factor)
  - first_time_nominee_flag: first Ballon d'Or nomination?
  - career_goals_vs_prior_avg: breakout detection (this year vs career avg)

All computed from existing ground truth + career stats — no new scraping.
These are TEMPORAL features, not interactions with current-season features,
so they should NOT have the multicollinearity problem that killed P0-A.
"""
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import json
from pathlib import Path

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
GT_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"
STATS_JSONL = PROJECT_ROOT / "data" / "raw" / "stats" / "stats_raw.jsonl"


def main():
    print("=" * 70)
    print("P1-D: Career Narrative Arc Features")
    print("=" * 70)

    df = pd.read_parquet(FEATURES_PARQUET)
    gt = pd.read_parquet(GT_PARQUET)

    # 1. years_in_top_5: how many prior years did this player finish top-5?
    # This captures the "overdue" factor — voters may reward a player who's
    # been close many times but never won.
    print("\nComputing years_in_top_5...")
    top5_history = {}  # player_name -> list of years they finished top-5
    for _, row in gt.sort_values("award_year").iterrows():
        player = row["player_name_raw"]
        year = int(row["award_year"])
        rank = int(row["rank"])
        if player not in top5_history:
            top5_history[player] = []
        if rank <= 5:
            top5_history[player].append(year)

    df["years_in_top_5"] = 0
    for idx, row in df.iterrows():
        player = row["player_name_raw"]
        year = int(row["award_year"])
        prior_top5 = [y for y in top5_history.get(player, []) if y < year]
        df.at[idx, "years_in_top_5"] = len(prior_top5)

    # 2. first_time_nominee_flag: is this the player's first Ballon d'Or nomination?
    print("Computing first_time_nominee_flag...")
    nomination_history = {}  # player_name -> set of years nominated
    for _, row in gt.sort_values("award_year").iterrows():
        player = row["player_name_raw"]
        year = int(row["award_year"])
        if player not in nomination_history:
            nomination_history[player] = set()
        nomination_history[player].add(year)

    df["first_time_nominee_flag"] = False
    for idx, row in df.iterrows():
        player = row["player_name_raw"]
        year = int(row["award_year"])
        prior_nominations = {y for y in nomination_history.get(player, set()) if y < year}
        df.at[idx, "first_time_nominee_flag"] = len(prior_nominations) == 0

    # 3. prior_nominations_count: total prior nominations (not just first-time)
    print("Computing prior_nominations_count...")
    df["prior_nominations_count"] = 0
    for idx, row in df.iterrows():
        player = row["player_name_raw"]
        year = int(row["award_year"])
        prior = {y for y in nomination_history.get(player, set()) if y < year}
        df.at[idx, "prior_nominations_count"] = len(prior)

    # 4. prior_winner_count: how many times has this player WON before?
    # (Different from previous_ballon_dor_winner which is just a boolean)
    print("Computing prior_winner_count...")
    winner_history = {}  # player_name -> list of years won
    for _, row in gt[gt["rank"] == 1].sort_values("award_year").iterrows():
        player = row["player_name_raw"]
        year = int(row["award_year"])
        if player not in winner_history:
            winner_history[player] = []
        winner_history[player].append(year)

    df["prior_winner_count"] = 0
    for idx, row in df.iterrows():
        player = row["player_name_raw"]
        year = int(row["award_year"])
        prior_wins = [y for y in winner_history.get(player, []) if y < year]
        df.at[idx, "prior_winner_count"] = len(prior_wins)

    # 5. years_since_new_winner: voter fatigue signal
    # How many years since a FIRST-TIME winner won?
    print("Computing years_since_new_winner...")
    first_time_winner_years = []
    for _, row in gt[gt["rank"] == 1].sort_values("award_year").iterrows():
        player = row["player_name_raw"]
        year = int(row["award_year"])
        prior_wins = [y for y in winner_history.get(player, []) if y < year]
        if len(prior_wins) == 0:
            first_time_winner_years.append(year)

    df["years_since_new_winner"] = 0
    for idx, row in df.iterrows():
        year = int(row["award_year"])
        prior_first_time_wins = [y for y in first_time_winner_years if y < year]
        if prior_first_time_wins:
            df.at[idx, "years_since_new_winner"] = year - max(prior_first_time_wins)
        else:
            df.at[idx, "years_since_new_winner"] = year - 1956  # since inception

    # Summary
    print("\nFeature summary:")
    print(f"  years_in_top_5: mean={df['years_in_top_5'].mean():.1f}, max={df['years_in_top_5'].max()}")
    print(f"  first_time_nominee_flag: {df['first_time_nominee_flag'].sum()}/{len(df)} True ({100*df['first_time_nominee_flag'].sum()/len(df):.1f}%)")
    print(f"  prior_nominations_count: mean={df['prior_nominations_count'].mean():.1f}, max={df['prior_nominations_count'].max()}")
    print(f"  prior_winner_count: {df['prior_winner_count'].sum()} total non-zero, max={df['prior_winner_count'].max()}")
    print(f"  years_since_new_winner: mean={df['years_since_new_winner'].mean():.1f}")

    # Sanity check
    print("\nSanity check — narrative winners:")
    for year, player in [(2021, "Lionel Messi"), (2024, "Rodri"), (2018, "Luka Modrić"),
                         (2022, "Karim Benzema"), (2008, "Cristiano Ronaldo")]:
        sub = df[(df["award_year"] == year) & (df["player_name_raw"] == player)]
        if len(sub):
            r = sub.iloc[0]
            print(f"  {year} {player:20}: years_in_top5={r['years_in_top_5']}, "
                  f"first_nominee={r['first_time_nominee_flag']}, "
                  f"prior_noms={r['prior_nominations_count']}, "
                  f"prior_wins={r['prior_winner_count']}, "
                  f"yrs_since_new={r['years_since_new_winner']}")

    # Save
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, FEATURES_PARQUET, compression="snappy")
    print(f"\n✅ Updated {FEATURES_PARQUET} ({df.shape[1]} cols)")

    # Add to feature lists
    new_features = [
        "years_in_top_5",
        "first_time_nominee_flag",
        "prior_nominations_count",
        "prior_winner_count",
        "years_since_new_winner",
    ]
    print(f"\nAdding {len(new_features)} new features to feature lists...")
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
        if "years_in_top_5" not in content:
            content = content.replace(
                '"position_adjusted_xg_contribution",',
                '"position_adjusted_xg_contribution",\n    "years_in_top_5", "first_time_nominee_flag",\n    "prior_nominations_count", "prior_winner_count", "years_since_new_winner",',
            )
            path.write_text(content)
            try:
                compile(content, str(path), "exec")
                print(f"  ✅ {filepath}")
            except SyntaxError as e:
                print(f"  ❌ {filepath} at line {e.lineno}: {e.msg}")
        else:
            print(f"  ⏭️  {filepath} (already has features)")

    # Update TIER_A_WEIGHTS
    path = PROJECT_ROOT / "validation/run_validation.py"
    content = path.read_text()
    if '"years_in_top_5":' not in content:
        content = content.replace(
            '"position_adjusted_xg_contribution": 0.3,\n',
            '"position_adjusted_xg_contribution": 0.3,\n'
            '    "years_in_top_5": 0.3,\n'
            '    "first_time_nominee_flag": 0.5,\n'
            '    "prior_nominations_count": 0.2,\n'
            '    "prior_winner_count": 0.8,\n'
            '    "years_since_new_winner": 0.3,\n',
        )
        path.write_text(content)
        try:
            compile(content, str(path), "exec")
            print(f"  ✅ TIER_A_WEIGHTS updated")
        except SyntaxError as e:
            print(f"  ❌ TIER_A_WEIGHTS at line {e.lineno}: {e.msg}")

    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "inference"))
    if "predict_season" in sys.modules: del sys.modules["predict_season"]
    from predict_season import FEATURES
    print(f"\nFinal FEATURES list ({len(FEATURES)} features):")
    for f in FEATURES:
        print(f"  {f}")


if __name__ == "__main__":
    main()
