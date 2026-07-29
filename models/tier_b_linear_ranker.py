"""Tier B — Pairwise linear ranker.

Per Architecture Blueprint §4.5:
  Learn w_i via pairwise logistic regression over within-season pairs
  (did player A rank above player B). Fully interpretable coefficients —
  this is likely the primary deliverable model given the interpretability
  requirement and small-N dataset.

Per Implementation Plan Phase 5 task 2:
  Implement pairwise logistic ranking, train, extract coefficients,
  sanity-check coefficient signs/magnitudes against football-domain
  intuition (e.g., trophy-win coefficient should be positive and
  non-trivial; a nonsensical sign on an important feature is a bug
  signal, not a 'surprising finding,' until proven otherwise).

Per Key Focus Areas §8 (validation discipline):
  - Train on all NON-held-out seasons only (excludes {2018, 2019, 2021,
    2022, 2023, 2024, 2025}).
  - Held-out seasons get looked at exactly ONCE at the end of Phase 6.
  - Use leave-one-season-out CV for hyperparameter decisions during Phase 5.

Pair construction:
  For each season, generate all (winner, loser) pairs from the candidate
  pool where rank_A < rank_B (A finished above B). Each pair becomes a
  training example: features = features_A - features_B, label = 1.
  The model learns weights w such that w · (features_A - features_B) > 0
  for pairs where A ranked higher.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
OUTPUT_PARQUET = PROJECT_ROOT / "data" / "processed" / "tier_b_scores.parquet"
COEFFICIENTS_JSON = PROJECT_ROOT / "models" / "tier_b_coefficients.json"
METRICS_JSON = PROJECT_ROOT / "models" / "tier_b_metrics.json"

# Held-out test seasons per Architecture Blueprint §4.6 + Key Focus Areas §8
HELD_OUT_SEASONS = {2018, 2019, 2021, 2022, 2023, 2024, 2025}

# Features used by Tier B (subset that's stable across eras + captures main signal)
# Excluding raw counts (which have cross-era inflation issues per Key Focus Area §7)
# in favor of peer-percentile features. Including trophy flags (era-stable).
TIER_B_FEATURES = [
    # Peer-relative (era-comparable)
    "goals_percentile_in_year",
    "assists_percentile_in_year",
    "apps_percentile_in_year",
    "minutes_percentile_in_year",
    # Trophy/team success
    "ucl_winner",
    "ucl_runner_up",
    "domestic_league_winner",
    "domestic_league_runner_up",
    # International tournament
    "world_cup_winner",
    "world_cup_runner_up",
    "euro_winner",
    "copa_america_winner",
    "international_tournament_year",
    # Narrative
    "signature_moment",
    "club_prestige_tier",
    "previous_ballon_dor_winner",
    "has_stats_data", "data_completeness_score",
    # Total production (raw, but useful for within-year comparison since
    # peer-percentile already captures cross-era normalization)
    "total_goals",
    "total_assists",
    "total_apps",
    "international_goals",
    "international_apps",
]


def load_features() -> pd.DataFrame:
    df = pd.read_parquet(FEATURES_PARQUET)
    return df


def build_pairs(df: pd.DataFrame, feature_cols: list[str]) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Build pairwise training examples.

    For each season, generate all (A, B) pairs where rank_A < rank_B
    (A finished above B). Each pair becomes a training example with
    features = features_A - features_B, label = 1.

    To balance, also include the reverse pair (B, A) with label = 0.

    Returns (X, y, metadata) where metadata is a list of dicts with
    season_id, player_a, player_b for traceability.
    """
    X_rows = []
    y_labels = []
    metadata = []

    for season_id, group in df.groupby("season_id"):
        # Sort by rank ascending (rank 1 = winner)
        group_sorted = group.sort_values("rank")
        rows = group_sorted.to_dict("records")

        # Generate all pairs (A, B) where A ranks higher (lower rank number)
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a = rows[i]   # higher-ranked (lower rank number)
                b = rows[j]   # lower-ranked

                # Feature difference
                feat_diff = []
                for col in feature_cols:
                    val_a = a.get(col)
                    val_b = b.get(col)
                    # Handle NaN
                    if val_a is None or (isinstance(val_a, float) and pd.isna(val_a)):
                        val_a = 0
                    if val_b is None or (isinstance(val_b, float) and pd.isna(val_b)):
                        val_b = 0
                    # Convert bool to int
                    if isinstance(val_a, bool):
                        val_a = int(val_a)
                    if isinstance(val_b, bool):
                        val_b = int(val_b)
                    feat_diff.append(val_a - val_b)

                X_rows.append(feat_diff)
                y_labels.append(1)  # A is the higher-ranked
                metadata.append({
                    "season_id": season_id,
                    "player_a": a["player_name_raw"],
                    "player_b": b["player_name_raw"],
                    "rank_a": a["rank"],
                    "rank_b": b["rank"],
                })

                # Reverse pair (for class balance)
                X_rows.append([-x for x in feat_diff])
                y_labels.append(0)
                metadata.append({
                    "season_id": season_id,
                    "player_a": b["player_name_raw"],
                    "player_b": a["player_name_raw"],
                    "rank_a": b["rank"],
                    "rank_b": a["rank"],
                })

    return np.array(X_rows, dtype=float), np.array(y_labels, dtype=int), metadata


def compute_yearly_metrics(df: pd.DataFrame, score_col: str) -> dict:
    """Compute top-1, top-3, top-5 accuracy + Spearman correlation per year."""
    from scipy.stats import spearmanr
    correct_top1 = 0
    correct_top3 = 0
    correct_top5 = 0
    total_years = 0
    spearman_sum = 0.0
    yearly = []
    for year, group in df.groupby("award_year"):
        total_years += 1
        sorted_group = group.sort_values(score_col, ascending=False)
        pred_top1 = sorted_group.iloc[0]["player_name_raw"]
        actual_top1_row = group[group["rank"] == 1]
        actual_top1 = actual_top1_row["player_name_raw"].iloc[0] if len(actual_top1_row) else None
        if pred_top1 == actual_top1:
            correct_top1 += 1
        pred_top3 = sorted_group.head(3)["player_name_raw"].tolist()
        pred_top5 = sorted_group.head(5)["player_name_raw"].tolist()
        if actual_top1 in pred_top3:
            correct_top3 += 1
        if actual_top1 in pred_top5:
            correct_top5 += 1
        # Spearman correlation between predicted score and actual rank
        # (negative because higher score = lower rank number)
        if len(group) >= 2:
            rho, _ = spearmanr(group[score_col], -group["rank"])
            if not pd.isna(rho):
                spearman_sum += rho
        yearly.append({
            "year": year,
            "actual_winner": actual_top1,
            "predicted_top1": pred_top1,
            "top1_correct": pred_top1 == actual_top1,
        })
    return {
        "top1_accuracy": correct_top1 / total_years if total_years else 0,
        "top3_hit_rate": correct_top3 / total_years if total_years else 0,
        "top5_hit_rate": correct_top5 / total_years if total_years else 0,
        "spearman_mean": spearman_sum / total_years if total_years else 0,
        "total_years": total_years,
        "yearly_results": yearly,
    }


def main() -> None:
    print("=" * 70)
    print("Tier B — Pairwise linear ranker")
    print("=" * 70)

    df = load_features()
    print(f"Loaded {len(df)} feature rows")

    # Split into train and held-out
    df_train = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    df_heldout = df[df["award_year"].isin(HELD_OUT_SEASONS)]
    print(f"Train seasons: {df_train['award_year'].nunique()} ({len(df_train)} rows)")
    print(f"Held-out seasons: {df_heldout['award_year'].nunique()} ({len(df_heldout)} rows) — NOT used for training")

    # Build pairs from training data only
    print(f"\nBuilding pairwise training examples from {len(TIER_B_FEATURES)} features...")
    X, y, metadata = build_pairs(df_train, TIER_B_FEATURES)
    print(f"Generated {len(X)} pairs ({len(X) // 2} unique pairs × 2 for class balance)")

    # Standardize features (helps regularization + interpretability)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Train logistic regression with L2 regularization
    # C=1.0 is a reasonable default; will tune via LOSO CV in Phase 6
    print("\nTraining pairwise logistic regression (C=1.0, L2 regularization)...")
    model = LogisticRegression(
        C=1.0,
        penalty="l2",
        solver="lbfgs",
        max_iter=1000,
        random_state=20260728,
    )
    model.fit(X_scaled, y)

    train_acc = model.score(X_scaled, y)
    print(f"Training pair accuracy: {train_acc:.1%}")

    # Extract coefficients
    coefs = model.coef_[0]
    intercept = model.intercept_[0]
    print(f"\nCoefficients (sorted by absolute value):")
    coef_data = []
    for feature, coef in sorted(zip(TIER_B_FEATURES, coefs), key=lambda x: -abs(x[1])):
        print(f"  {feature:40}  {coef:+.4f}")
        coef_data.append({"feature": feature, "coefficient": float(coef)})
    print(f"  {'(intercept)':40}  {intercept:+.4f}")

    # Sanity check coefficient signs against football-domain intuition
    print("\n" + "=" * 70)
    print("Sanity check — coefficient signs vs football intuition")
    print("=" * 70)
    expected_signs = {
        "goals_percentile_in_year": "+",
        "assists_percentile_in_year": "+",
        "apps_percentile_in_year": "+",
        "minutes_percentile_in_year": "+",
        "ucl_winner": "+",
        "ucl_runner_up": "+",
        "domestic_league_winner": "+",
        "domestic_league_runner_up": "+",
        "world_cup_winner": "+",
        "world_cup_runner_up": "+",
        "euro_winner": "+",
        "copa_america_winner": "+",
        "international_tournament_year": "+",
                "club_prestige_tier": "-",  # tier 1 = best, so negative coefficient
        "previous_ballon_dor_winner": "+",
        "has_stats_data": "+",
        "data_completeness_score": "+",
        "total_goals": "+",
        "total_assists": "+",
        "total_apps": "+",
        "international_goals": "+",
        "international_apps": "+",
    }
    sign_issues = 0
    for feature, expected in expected_signs.items():
        if feature not in TIER_B_FEATURES:
            continue
        coef = coefs[TIER_B_FEATURES.index(feature)]
        actual = "+" if coef > 0 else ("-" if coef < 0 else "0")
        if actual != expected and abs(coef) > 0.01:
            marker = "❌"
            sign_issues += 1
        else:
            marker = "✅"
        print(f"  {marker} {feature:40}  expected={expected} actual={actual}  coef={coef:+.4f}")

    if sign_issues > 0:
        print(f"\n⚠️ {sign_issues} coefficient sign issues detected.")
        print("Per Implementation Plan Phase 5 task 2, a nonsensical sign on an")
        print("important feature is a bug signal, not a 'surprising finding,' until")
        print("proven otherwise. Investigate before trusting this model.")
    else:
        print(f"\n✅ All coefficient signs match football-domain intuition.")

    # Apply model to compute Tier B scores for ALL rows (including held-out)
    # Score for a single player = w · features (not a pair difference).
    # This is valid because the model learned to rank pairs by sign of
    # w · (features_A - features_B), which means single-player score = w · features.
    print("\nComputing Tier B scores for all rows...")
    all_features = df[TIER_B_FEATURES].copy()
    # Convert bool to int, fill NaN with 0
    for col in all_features.columns:
        if all_features[col].dtype == bool:
            all_features[col] = all_features[col].astype(int)
        all_features[col] = all_features[col].fillna(0)
    all_features_scaled = scaler.transform(all_features.values)
    scores = model.decision_function(all_features_scaled)  # w · x (no sigmoid)
    df_with_scores = df.copy()
    df_with_scores["tier_b_score"] = scores

    # Compute metrics on TRAINING data (sanity check — should be > Tier A)
    print("\n" + "=" * 70)
    print("Tier B metrics on TRAINING data (sanity check)")
    print("=" * 70)
    train_metrics = compute_yearly_metrics(df_with_scores[~df_with_scores["award_year"].isin(HELD_OUT_SEASONS)], "tier_b_score")
    print(f"  Top-1 accuracy: {train_metrics['top1_accuracy']:.1%}")
    print(f"  Top-3 hit rate: {train_metrics['top3_hit_rate']:.1%}")
    print(f"  Top-5 hit rate: {train_metrics['top5_hit_rate']:.1%}")
    print(f"  Mean Spearman ρ: {train_metrics['spearman_mean']:.3f}")

    # Save scores
    out_df = df_with_scores[["season_id", "award_year", "player_name_raw", "rank", "tier_b_score"]].copy()
    table = pa.Table.from_pandas(out_df, preserve_index=False)
    pq.write_table(table, OUTPUT_PARQUET, compression="snappy")
    print(f"\n✅ Wrote {OUTPUT_PARQUET}")

    # Save coefficients
    coef_json = {
        "feature_order": TIER_B_FEATURES,
        "coefficients": [float(c) for c in coefs],
        "intercept": float(intercept),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "training_pairs": len(X),
        "training_pair_accuracy": float(train_acc),
        "training_metrics": {
            "top1_accuracy": train_metrics["top1_accuracy"],
            "top3_hit_rate": train_metrics["top3_hit_rate"],
            "top5_hit_rate": train_metrics["top5_hit_rate"],
            "spearman_mean": train_metrics["spearman_mean"],
        },
        "sign_issues_count": sign_issues,
        "held_out_seasons": sorted(list(HELD_OUT_SEASONS)),
    }
    COEFFICIENTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(COEFFICIENTS_JSON, "w") as f:
        json.dump(coef_json, f, indent=2)
    print(f"✅ Wrote {COEFFICIENTS_JSON}")

    # Save metrics (for comparison with Tier A and Tier C)
    metrics_json = {
        "tier": "B",
        "model": "pairwise_logistic_regression",
        "features_used": TIER_B_FEATURES,
        "training_pairs": len(X),
        "training_pair_accuracy": float(train_acc),
        "training_yearly_metrics": {
            "top1_accuracy": train_metrics["top1_accuracy"],
            "top3_hit_rate": train_metrics["top3_hit_rate"],
            "top5_hit_rate": train_metrics["top5_hit_rate"],
            "spearman_mean": train_metrics["spearman_mean"],
            "total_years": train_metrics["total_years"],
        },
        "sign_issues_count": sign_issues,
        "held_out_seasons": sorted(list(HELD_OUT_SEASONS)),
        "note": "Held-out seasons NOT evaluated here — deferred to Phase 6 final eval per Key Focus Areas §8.",
    }
    with open(METRICS_JSON, "w") as f:
        json.dump(metrics_json, f, indent=2)
    print(f"✅ Wrote {METRICS_JSON}")

    print()
    print("=" * 70)
    print("Tier B summary:")
    print(f"  Training pair accuracy: {train_acc:.1%}")
    print(f"  Training top-1 accuracy: {train_metrics['top1_accuracy']:.1%}")
    print(f"  Training top-3 hit rate: {train_metrics['top3_hit_rate']:.1%}")
    print(f"  Training top-5 hit rate: {train_metrics['top5_hit_rate']:.1%}")
    print(f"  Training mean Spearman ρ: {train_metrics['spearman_mean']:.3f}")
    print(f"  Coefficient sign issues: {sign_issues}")
    print("=" * 70)


if __name__ == "__main__":
    main()
