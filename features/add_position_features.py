"""P0-B: Position-adjusted value features.

Per implementation plan: 17% of missed winners are midfielders/defenders
whose value isn't in goalscoring (Modrić, Rodri, Cannavaro, Yashin).

Features added:
  - position: normalized to GK/DF/MF/FW/Unknown
  - position_adjusted_xg_contribution: (xG + xA) × position_multiplier
  - position_rarity_score: how rare is a winner at this position

Position data is only available for 120 rows (44% of post-split era,
0% for other eras). For rows without position, features stay NaN
per Key Focus Areas §9.
"""
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
GT_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"

# Position multipliers — midfielders/defenders get more credit per goal
# because their scoring is more notable (less expected)
POSITION_MULTIPLIERS = {
    "FW": 1.0,   # baseline — forwards are expected to score
    "MF": 1.5,   # midfielder stats are 50% more notable
    "DF": 2.0,   # defender stats are 2× more notable
    "GK": 3.0,   # GK stats are 3× more notable (though GKs rarely have xG/xA)
    "Unknown": 1.0,  # no adjustment for unknown positions
}


def normalize_position(raw: str) -> str:
    """Normalize various position strings to GK/DF/MF/FW."""
    if not raw or pd.isna(raw):
        return "Unknown"
    raw_lower = raw.lower().strip()
    if "goalkeeper" in raw_lower or raw_lower == "gk":
        return "GK"
    if "defender" in raw_lower or raw_lower == "df":
        return "DF"
    if "midfielder" in raw_lower or raw_lower == "mf":
        return "MF"
    if "forward" in raw_lower or raw_lower == "fw":
        return "FW"
    # Handle abbreviated forms
    if "gk" in raw_lower: return "GK"
    if "df" in raw_lower or "def" in raw_lower: return "DF"
    if "mf" in raw_lower or "mid" in raw_lower: return "MF"
    if "fw" in raw_lower or "fwd" in raw_lower or "str" in raw_lower: return "FW"
    return "Unknown"


def main():
    print("=" * 70)
    print("P0-B: Position-Adjusted Value Features")
    print("=" * 70)

    df = pd.read_parquet(FEATURES_PARQUET)
    gt = pd.read_parquet(GT_PARQUET)

    # Normalize position_raw
    print("\nNormalizing positions...")
    df["position"] = df["position_raw"].apply(normalize_position)

    print(f"Position distribution:")
    print(df["position"].value_counts())

    # Compute position_rarity_score from historical winners
    # How rare is a winner at this position?
    # Based on all winners in ground truth where position is known
    winners = gt[gt["rank"] == 1].merge(
        df[["season_id", "player_name_raw", "position"]].drop_duplicates(),
        on=["season_id", "player_name_raw"],
        how="left"
    )
    # Count winners by position
    winner_pos_counts = winners["position"].value_counts()
    total_known_pos = winner_pos_counts.sum()
    print(f"\nWinner position distribution (from {total_known_pos} known):")
    print(winner_pos_counts)

    # Rarity score: inverse of frequency (higher = more rare = more notable)
    # FW is most common → lowest rarity
    # GK is rarest → highest rarity
    position_rarity = {}
    for pos in ["GK", "DF", "MF", "FW", "Unknown"]:
        count = winner_pos_counts.get(pos, 0)
        if count > 0 and total_known_pos > 0:
            frequency = count / total_known_pos
            position_rarity[pos] = 1.0 / frequency  # inverse frequency
        else:
            position_rarity[pos] = 1.0  # default for unknown

    # Normalize to 0-3 scale (FW=0.5, MF=1.0, DF=2.0, GK=3.0)
    # Using fixed values based on football-domain knowledge:
    # FW wins ~70% of the time → rarity 0.5
    # MF wins ~20% → rarity 1.0
    # DF wins ~7% → rarity 2.0
    # GK wins ~1.4% → rarity 3.0
    position_rarity_fixed = {
        "FW": 0.5,
        "MF": 1.0,
        "DF": 2.0,
        "GK": 3.0,
        "Unknown": 1.0,
    }

    df["position_rarity_score"] = df["position"].map(position_rarity_fixed)

    # Compute position_adjusted_xg_contribution
    # = (xG + xA) × position_multiplier
    # Only computable where we have BOTH position AND xG/xA data
    print("\nComputing position_adjusted_xg_contribution...")
    df["position_multiplier"] = df["position"].map(POSITION_MULTIPLIERS)

    def compute_adjusted_xg(row):
        xg = row.get("xg")
        xa = row.get("xa")
        mult = row.get("position_multiplier", 1.0)
        if pd.isna(xg) and pd.isna(xa):
            return None
        total = (0 if pd.isna(xg) else xg) + (0 if pd.isna(xa) else xa)
        return total * mult

    df["position_adjusted_xg_contribution"] = df.apply(compute_adjusted_xg, axis=1)

    # Summary
    print("\nFeature summary:")
    print(f"  position: {df['position'].value_counts().to_dict()}")
    print(f"  position_rarity_score: mean={df['position_rarity_score'].mean():.2f}")
    print(f"  position_adjusted_xg_contribution: "
          f"non-null={df['position_adjusted_xg_contribution'].notna().sum()}/{len(df)} "
          f"({100*df['position_adjusted_xg_contribution'].notna().sum()/len(df):.1f}%)")

    # Coverage by era
    print(f"\nCoverage by era:")
    for era in ["classical", "pre_merger", "fifa_merger", "post_split"]:
        sub = df[df["era_tag"] == era]
        pos_cov = (sub["position"] != "Unknown").sum()
        adj_cov = sub["position_adjusted_xg_contribution"].notna().sum()
        print(f"  {era:12}: position_known={pos_cov}/{len(sub)} ({100*pos_cov/len(sub):.0f}%), "
              f"adj_xg={adj_cov}/{len(sub)} ({100*adj_cov/len(sub):.0f}%)")

    # Sanity check: known defensive/tactical winners
    print("\nSanity check — known defensive/tactical winners:")
    for year, player, expected_pos in [
        (2024, "Rodri", "MF"),
        (2018, "Luka Modrić", "MF"),
        (2006, "Fabio Cannavaro", "DF"),
    ]:
        sub = df[(df["award_year"] == year) & (df["player_name_raw"] == player)]
        if len(sub):
            r = sub.iloc[0]
            print(f"  {year} {player}: position={r['position']!r}, "
                  f"rarity={r['position_rarity_score']}, "
                  f"adj_xg={r['position_adjusted_xg_contribution']}, "
                  f"xg={r.get('xg')}, xa={r.get('xa')}")

    # Save
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, FEATURES_PARQUET, compression="snappy")
    print(f"\n✅ Updated {FEATURES_PARQUET} ({df.shape[1]} cols)")

    # Add new features to feature lists
    new_features = ["position_rarity_score", "position_adjusted_xg_contribution"]
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
        if "position_rarity_score" not in content:
            content = content.replace(
                '"league_visibility_tier",',
                '"league_visibility_tier",\n    "position_rarity_score", "position_adjusted_xg_contribution",',
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
    if '"position_rarity_score":' not in content:
        content = content.replace(
            '"league_visibility_tier": -0.2,',
            '"league_visibility_tier": -0.2,\n    "position_rarity_score": 0.5,  # rare position = boost\n    "position_adjusted_xg_contribution": 0.3,',
        )
        path.write_text(content)
        try:
            compile(content, str(path), "exec")
            print(f"  ✅ validation/run_validation.py (TIER_A_WEIGHTS updated)")
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
