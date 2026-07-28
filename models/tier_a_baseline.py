"""Tier A — Explicit weighted-sum baseline scorer.

Per Architecture Blueprint §4.5:
  A fully transparent formula: score = Σ w_i * feature_i with manually
  reasoned starting weights (not learned) reflecting football-domain
  understanding of what the jury has said it values. This is the sanity-
  check floor — nothing more complex is worth keeping if it can't beat this.

Per Implementation Plan Phase 5 task 1:
  Implement the explicit weighted-sum formula with manually reasoned
  starting weights. Run it against the full dataset, record baseline
  metrics. This is a non-learned reference point — do not skip it, even
  though it feels primitive; it is the floor every later model must beat.

The weights below are manually reasoned from football-domain knowledge:
  - UCL winner is the single most important team-achievement signal
  - World Cup winner in a World Cup year is a massive boost
  - Domestic league title matters but less than UCL
  - Peer-percentile features matter more than raw counts for cross-era
    comparability per Key Focus Area §7
  - Previous Ballon d'Or winner gets a reputational boost (documented
    voting pattern, per Key Focus Area §3)
  - Club prestige tier matters (media-market bias per Key Focus Area §5)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
WEIGHTS_YAML = PROJECT_ROOT / "models" / "tier_a_weights.yaml"
OUTPUT_PARQUET = PROJECT_ROOT / "data" / "processed" / "tier_a_scores.parquet"


# -----------------------------------------------------------------------------
# Manually reasoned weights (NOT learned)
# -----------------------------------------------------------------------------

WEIGHTS = {
    # Family 1: Individual production — peer-percentile features get higher
    # weight than raw counts per Key Focus Area §7 (cross-era comparability)
    "goals_percentile_in_year": 2.0,
    "assists_percentile_in_year": 1.0,
    "apps_percentile_in_year": 0.3,
    "minutes_percentile_in_year": 0.2,

    # Family 2: Trophy/team success
    "ucl_winner": 3.0,
    "ucl_runner_up": 1.0,
    "domestic_league_winner": 1.5,
    "domestic_league_runner_up": 0.3,

    # Family 3: International tournament
    "world_cup_winner": 3.5,
    "world_cup_runner_up": 1.2,
    "euro_winner": 2.0,
    "copa_america_winner": 1.8,
    "international_tournament_year": 0.5,

    # Family 7: Narrative
    "signature_moment": 1.5,
    "club_prestige_tier": -0.5,  # Negative: tier 1 = best
    "previous_ballon_dor_winner": 1.0,
}


def compute_tier_a_score(features: pd.DataFrame) -> pd.Series:
    """Compute the Tier A heuristic score for each row.

    Missing values (NaN) are treated as 0 in the weighted sum (i.e., they
    contribute nothing). This is consistent with Key Focus Area §9 — we
    don't impute, we just don't add any signal where data is missing.
    """
    scores = pd.Series(0.0, index=features.index, name="tier_a_score")
    for feature, weight in WEIGHTS.items():
        if feature not in features.columns:
            print(f"  ⚠️ Feature {feature!r} not in feature matrix — skipping")
            continue
        col = features[feature]
        if col.dtype == bool:
            col = col.astype(int)
        col = col.fillna(0)
        scores += weight * col
    return scores


def main() -> None:
    print("=" * 70)
    print("Tier A — Heuristic weighted-sum baseline scorer")
    print("=" * 70)

    df = pd.read_parquet(FEATURES_PARQUET)
    print(f"Loaded {len(df)} feature rows")

    print(f"\nManually-reasoned weights ({len(WEIGHTS)} features):")
    for feature, weight in sorted(WEIGHTS.items(), key=lambda x: -abs(x[1])):
        print(f"  {feature:40}  {weight:+.2f}")

    print("\nComputing Tier A scores...")
    scores = compute_tier_a_score(df)
    df_with_scores = df.copy()
    df_with_scores["tier_a_score"] = scores

    # Sanity check — top-1 and top-3 accuracy per year
    print("\n" + "=" * 70)
    print("Sanity check — Tier A top-1 / top-3 accuracy per year")
    print("=" * 70)

    correct_top1 = 0
    correct_top3 = 0
    total_years = 0
    yearly_results = []
    for year, group in df_with_scores.groupby("award_year"):
        total_years += 1
        sorted_group = group.sort_values("tier_a_score", ascending=False)
        predicted_top1 = sorted_group.iloc[0]["player_name_raw"]
        actual_top1_row = group[group["rank"] == 1]
        actual_top1 = actual_top1_row["player_name_raw"].iloc[0] if len(actual_top1_row) else None
        is_correct = predicted_top1 == actual_top1
        if is_correct:
            correct_top1 += 1
        predicted_top3 = sorted_group.head(3)["player_name_raw"].tolist()
        if actual_top1 in predicted_top3:
            correct_top3 += 1
        actual_rank_in_pred = None
        if actual_top1:
            pred_list = sorted_group["player_name_raw"].tolist()
            if actual_top1 in pred_list:
                actual_rank_in_pred = pred_list.index(actual_top1) + 1
        actual_score = None
        if actual_top1:
            actual_score_row = group[group["player_name_raw"] == actual_top1]["tier_a_score"]
            if len(actual_score_row):
                actual_score = actual_score_row.iloc[0]
        yearly_results.append({
            "year": year,
            "actual_winner": actual_top1,
            "predicted_top1": predicted_top1,
            "predicted_top1_score": float(sorted_group.iloc[0]["tier_a_score"]),
            "actual_winner_score": float(actual_score) if actual_score is not None else None,
            "actual_winner_rank_in_prediction": actual_rank_in_pred,
            "top1_correct": bool(is_correct),
        })

    top1_acc = correct_top1 / total_years
    top3_acc = correct_top3 / total_years
    print(f"\nTop-1 accuracy: {correct_top1}/{total_years} ({top1_acc:.1%})")
    print(f"Top-3 hit rate: {correct_top3}/{total_years} ({top3_acc:.1%})")

    print("\nSample yearly results (first 15):")
    for r in yearly_results[:15]:
        marker = "✅" if r["top1_correct"] else "❌"
        print(f"  {r['year']}: {marker} predicted={r['predicted_top1']!r:30}  actual={r['actual_winner']!r:30}  (actual rank in pred: {r['actual_winner_rank_in_prediction']})")

    # Save scores
    out_df = df_with_scores[["season_id", "award_year", "player_name_raw", "rank", "tier_a_score"]].copy()
    table = pa.Table.from_pandas(out_df, preserve_index=False)
    pq.write_table(table, OUTPUT_PARQUET, compression="snappy")
    print(f"\n✅ Wrote {OUTPUT_PARQUET}")

    # Save weights YAML
    import yaml
    weights_data = {
        "_metadata": {
            "description": "Tier A manually-reasoned weights for the heuristic weighted-sum baseline.",
            "philosophy": "Manually reasoned from football-domain knowledge of what the Ballon d'Or jury has historically weighted. NOT learned. This is the sanity-check floor — every learned model (Tier B/C) must beat these metrics to justify its complexity.",
            "missing_value_handling": "NaN treated as 0 (no signal contribution, no imputation per Key Focus Areas §9).",
            "top1_accuracy": f"{top1_acc:.1%} ({correct_top1}/{total_years} years)",
            "top3_hit_rate": f"{top3_acc:.1%} ({correct_top3}/{total_years} years)",
        },
        "weights": WEIGHTS,
    }
    WEIGHTS_YAML.parent.mkdir(parents=True, exist_ok=True)
    with open(WEIGHTS_YAML, "w") as f:
        yaml.dump(weights_data, f, default_flow_style=False, sort_keys=False)
    print(f"✅ Wrote {WEIGHTS_YAML}")

    print()
    print("=" * 70)
    print(f"Tier A baseline metrics:")
    print(f"  Top-1 accuracy: {top1_acc:.1%}")
    print(f"  Top-3 hit rate: {top3_acc:.1%}")
    print("=" * 70)
    print()
    print("This is the floor. Tier B (pairwise linear) and Tier C (GBM) must")
    print("beat these metrics to justify their complexity.")


if __name__ == "__main__":
    main()
