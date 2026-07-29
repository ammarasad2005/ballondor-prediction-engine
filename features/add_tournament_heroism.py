"""P0-A: Tournament heroism features (simplified — from existing data).

Per implementation plan: 17% of missed winners won on tournament heroism
(Modrić 2018, Cannavaro 2006, Rodri 2024).

The full implementation requires per-match Understat data to distinguish
UCL knockout goals from group stage goals. That requires 835 API calls
to getPlayerData/{id} — deferred to a future iteration.

This simplified version derives tournament heroism proxies from EXISTING
data already in features.parquet:

1. continental_goals_ratio: what fraction of total goals came in
   continental competition (UCL/UEL)? High ratio = "big-game player"
2. international_goals_per_app: goal efficiency for national team
3. tournament_year_boost: interaction of international_tournament_year ×
   international_goals (captures: did they perform when it mattered most?)
4. ucl_winner_goalscorer: interaction of ucl_winner × continental_goals
   (captured: did they SCORE in the UCL-winning campaign?)

These are imperfect proxies but capture the core signal without new data.
"""
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"


def safe_div(a, b):
    if pd.isna(a) or pd.isna(b) or b == 0:
        return None
    return a / b


def main():
    print("=" * 70)
    print("P0-A: Tournament Heroism Features (simplified)")
    print("=" * 70)

    df = pd.read_parquet(FEATURES_PARQUET)

    # 1. continental_goals_ratio: fraction of total goals from continental competition
    # High ratio = player scores disproportionately in UCL = "big-game player"
    df["continental_goals_ratio"] = df.apply(
        lambda r: safe_div(r.get("continental_goals"), r.get("total_goals")),
        axis=1
    )

    # 2. international_goals_per_app: goal efficiency for national team
    # A player scoring 10 goals in 7 international apps is a tournament hero
    df["international_goals_per_app"] = df.apply(
        lambda r: safe_div(r.get("international_goals"), r.get("international_apps")),
        axis=1
    )

    # 3. tournament_year_boost: interaction of tournament_year × international_goals
    # Captures: in a World Cup/Euro/Copa year, did the player score for their country?
    # This is the Modrić 2018 signal (WC year + international goals = heroism)
    df["tournament_year_boost"] = df.apply(
        lambda r: (r.get("international_goals") or 0) if r.get("international_tournament_year") else 0,
        axis=1
    )

    # 4. ucl_winner_goalscorer: interaction of ucl_winner × continental_goals
    # Did they score in the UCL-winning campaign? (Not just rode the bench)
    df["ucl_winner_goalscorer"] = df.apply(
        lambda r: (r.get("continental_goals") or 0) if r.get("ucl_winner") else 0,
        axis=1
    )

    # 5. wc_winner_goalscorer: interaction of world_cup_winner × international_goals
    # Did they score for the WC-winning nation?
    df["wc_winner_goalscorer"] = df.apply(
        lambda r: (r.get("international_goals") or 0) if r.get("world_cup_winner") else 0,
        axis=1
    )

    # Summary
    print("\nFeature summary:")
    for col in ["continental_goals_ratio", "international_goals_per_app",
                "tournament_year_boost", "ucl_winner_goalscorer", "wc_winner_goalscorer"]:
        nn = df[col].notna().sum()
        nz = (df[col] > 0).sum()
        print(f"  {col:35}: non-null={nn}/{len(df)}, non-zero={nz}")

    # Sanity check
    print("\nSanity check — tournament heroism winners:")
    for year, player in [(2018, "Luka Modrić"), (2024, "Rodri"), (2022, "Karim Benzema"),
                         (2006, "Fabio Cannavaro"), (1998, "Zinedine Zidane")]:
        sub = df[(df["award_year"] == year) & (df["player_name_raw"] == player)]
        if len(sub):
            r = sub.iloc[0]
            print(f"  {year} {player:20}: cont_ratio={r.get('continental_goals_ratio')}, "
                  f"intl_per_app={r.get('international_goals_per_app')}, "
                  f"tourn_boost={r.get('tournament_year_boost')}, "
                  f"ucl_gs={r.get('ucl_winner_goalscorer')}, "
                  f"wc_gs={r.get('wc_winner_goalscorer')}")

    # Save
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, FEATURES_PARQUET, compression="snappy")
    print(f"\n✅ Updated {FEATURES_PARQUET} ({df.shape[1]} cols)")

    # Add to feature lists
    new_features = [
        "continental_goals_ratio",
        "international_goals_per_app",
        "tournament_year_boost",
        "ucl_winner_goalscorer",
        "wc_winner_goalscorer",
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
        if "continental_goals_ratio" not in content:
            content = content.replace(
                '"position_adjusted_xg_contribution",',
                '"position_adjusted_xg_contribution",\n    "continental_goals_ratio", "international_goals_per_app",\n    "tournament_year_boost", "ucl_winner_goalscorer", "wc_winner_goalscorer",',
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
    if '"continental_goals_ratio":' not in content:
        content = content.replace(
            '"position_adjusted_xg_contribution": 0.3,\n',
            '"position_adjusted_xg_contribution": 0.3,\n'
            '    "continental_goals_ratio": 0.5,\n'
            '    "international_goals_per_app": 0.5,\n'
            '    "tournament_year_boost": 1.0,\n'
            '    "ucl_winner_goalscorer": 0.5,\n'
            '    "wc_winner_goalscorer": 1.0,\n',
        )
        path.write_text(content)
        try:
            compile(content, str(path), "exec")
            print(f"  ✅ TIER_A_WEIGHTS updated")
        except SyntaxError as e:
            print(f"  ❌ TIER_A_WEIGHTS at line {e.lineno}: {e.msg}")

    # Print final feature list
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "inference"))
    if "predict_season" in sys.modules: del sys.modules["predict_season"]
    from predict_season import FEATURES
    print(f"\nFinal FEATURES list ({len(FEATURES)} features):")
    for f in FEATURES:
        print(f"  {f}")


if __name__ == "__main__":
    main()
