"""Tier C — Gradient-boosted ranker (XGBoost rank:ndcg).

Per Architecture Blueprint §4.5:
  XGBoost/LightGBM with rank:pairwise or rank:ndcg objective, group
  set per season. Higher capacity, higher overfitting risk given
  N≈300-350 candidate-seasons — must be regularized aggressively
  (shallow trees, strong L1/L2, small learning rate, early stopping).

Per Implementation Plan Phase 5 task 3:
  Implement XGBoost ranking objective with group-by-season, aggressive
  regularization defaults. Do not tune hyperparameters against the
  final held-out test set — only against validation folds.

Per Key Focus Areas §8:
  Train on non-held-out seasons only. Held-out seasons {2018, 2019,
  2021, 2022, 2023, 2024, 2025} NOT touched until Phase 6 final eval.

Per Architecture Blueprint §4.5 Tier D decision rule:
  Prefer B unless C shows a consistent, non-marginal improvement
  across multiple validation folds (not just one lucky split).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import spearmanr
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
OUTPUT_PARQUET = PROJECT_ROOT / "data" / "processed" / "tier_c_scores.parquet"
MODEL_JSON = PROJECT_ROOT / "models" / "tier_c_model.json"
METRICS_JSON = PROJECT_ROOT / "models" / "tier_c_metrics.json"

HELD_OUT_SEASONS = {2018, 2019, 2021, 2022, 2023, 2024, 2025}

# Same features as Tier B for direct comparison
TIER_C_FEATURES = [
    "goals_percentile_in_year",
    "assists_percentile_in_year",
    "apps_percentile_in_year",
    "minutes_percentile_in_year",
    "ucl_winner",
    "ucl_runner_up",
    "domestic_league_winner",
    "domestic_league_runner_up",
    "world_cup_winner",
    "world_cup_runner_up",
    "euro_winner",
    "copa_america_winner",
    "international_tournament_year",
    "club_prestige_tier",
    "previous_ballon_dor_winner",
    "has_stats_data",
    "data_completeness_score",
    "nation_prior_winners_count",
    "continent_prior_winners_count",
    "nation_years_since_last_winner",
    "league_visibility_tier",
    "position_rarity_score",
    "position_adjusted_xg_contribution",
    "years_in_top_5",
    "first_time_nominee_flag",
    "prior_nominations_count",
    "prior_winner_count",
    "years_since_new_winner",
    "total_goals",
    "total_assists",
    "total_apps",
    "international_goals",
    "international_apps",
    "xg",
    "xa",
    "npxg",
    "xg_per90",
    "xa_per90",
    "xg_overperformance",
]


def load_features() -> pd.DataFrame:
    df = pd.read_parquet(FEATURES_PARQUET)
    return df


def prepare_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Prepare features for XGBoost.

    XGBoost handles NaN natively, so we DON'T impute — leave NaN as-is.
    Convert bools to int.

    Returns (X, y, group) where:
      - X: feature matrix (n_rows × n_features)
      - y: target = -rank (XGBoost rank:ndcg expects higher = better,
            so we use -rank since rank 1 = winner = best)
      - group: list of group sizes (one group per season)
    """
    # Sort by season_id then by rank (within season) — required by XGBoost ranker
    df_sorted = df.sort_values(["season_id", "rank"]).reset_index(drop=True)

    X = df_sorted[TIER_C_FEATURES].copy()
    # Convert bools to int
    for col in X.columns:
        if X[col].dtype == bool:
            X[col] = X[col].astype(int)
    # Leave NaN as-is (XGBoost handles natively)

    # Target: rank-based relevance score for NDCG.
    # XGBoost rank:ndcg expects non-negative integer labels where higher = more relevant.
    # Convert rank to relevance: rank 1 = highest relevance.
    # Cap at 31 (XGBoost's default exponential NDCG gain limit).
    # Use: max(31 - rank + 1, 1) so winners get ~31, last place gets 1.
    y = np.maximum(31 - df_sorted["rank"].astype(int).values + 1, 1)

    # Group: one group per season
    group_sizes = df_sorted.groupby("season_id").size().tolist()

    return X.values, y, group_sizes, df_sorted


def compute_yearly_metrics(df: pd.DataFrame, score_col: str) -> dict:
    """Same as Tier B metrics."""
    correct_top1 = 0
    correct_top3 = 0
    correct_top5 = 0
    total_years = 0
    spearman_sum = 0.0
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
        if len(group) >= 2:
            rho, _ = spearmanr(group[score_col], -group["rank"])
            if not pd.isna(rho):
                spearman_sum += rho
    return {
        "top1_accuracy": correct_top1 / total_years if total_years else 0,
        "top3_hit_rate": correct_top3 / total_years if total_years else 0,
        "top5_hit_rate": correct_top5 / total_years if total_years else 0,
        "spearman_mean": spearman_sum / total_years if total_years else 0,
        "total_years": total_years,
    }


def main() -> None:
    print("=" * 70)
    print("Tier C — Gradient-boosted ranker (XGBoost rank:ndcg)")
    print("=" * 70)

    df = load_features()
    print(f"Loaded {len(df)} feature rows")

    # Split train / held-out
    df_train = df[~df["award_year"].isin(HELD_OUT_SEASONS)].copy()
    df_heldout = df[df["award_year"].isin(HELD_OUT_SEASONS)].copy()
    print(f"Train seasons: {df_train['award_year'].nunique()} ({len(df_train)} rows)")
    print(f"Held-out seasons: {df_heldout['award_year'].nunique()} ({len(df_heldout)} rows) — NOT used")

    # Prepare training data
    print(f"\nPreparing features ({len(TIER_C_FEATURES)} features, NaN left as-is for XGBoost)...")
    X_train, y_train, group_train, df_train_sorted = prepare_features(df_train)
    print(f"X_train shape: {X_train.shape}")
    print(f"y_train shape: {y_train.shape}")
    print(f"Groups (seasons): {len(group_train)}, sizes: min={min(group_train)}, max={max(group_train)}")

    # Build DMatrix
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dtrain.set_group(group_train)

    # Aggressive regularization per Architecture Blueprint §4.5
    # (small N ~ 1500 candidate-seasons in training)
    params = {
        "objective": "rank:ndcg",
        "eval_metric": "ndcg",
        "max_depth": 3,              # shallow trees
        "eta": 0.05,                 # small learning rate
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 1.0,            # L1
        "reg_lambda": 5.0,           # L2 (strong)
        "min_child_weight": 5,       # prevent overfitting on small groups
        "random_state": 20260728,
        "verbosity": 0,
    }
    print(f"\nXGBoost params: {params}")

    # Train with early stopping on training data (using 80/20 split within training)
    # We'll use leave-one-season-out CV in Phase 6 for proper validation.
    # For Phase 5, train on all training data with a fixed number of rounds.
    print("\nTraining XGBoost ranker (200 rounds, no early stopping — Phase 6 will do LOSO CV)...")
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=200,
        evals=[(dtrain, "train")],
        verbose_eval=False,
    )

    # Predict on training data (sanity check — should be > Tier B)
    print("\nComputing Tier C scores for all rows...")
    train_preds = model.predict(dtrain)

    # Compute scores for ALL rows (train + held-out) for downstream use
    # Predict per row, then attach back to the original (un-sorted) df
    X_all, _, _, df_all_sorted = prepare_features(df)
    dall = xgb.DMatrix(X_all)
    # Set group for prediction (XGBoost needs this for ranker models)
    dall.set_group(df.groupby("season_id").size().tolist())
    all_preds = model.predict(dall)

    # Attach predictions back to original df
    df_with_scores = df_all_sorted[["season_id", "award_year", "player_name_raw", "rank"]].copy()
    df_with_scores["tier_c_score"] = all_preds

    # Compute training metrics
    print("\n" + "=" * 70)
    print("Tier C metrics on TRAINING data (sanity check)")
    print("=" * 70)
    train_metrics = compute_yearly_metrics(
        df_with_scores[~df_with_scores["award_year"].isin(HELD_OUT_SEASONS)],
        "tier_c_score"
    )
    print(f"  Top-1 accuracy: {train_metrics['top1_accuracy']:.1%}")
    print(f"  Top-3 hit rate: {train_metrics['top3_hit_rate']:.1%}")
    print(f"  Top-5 hit rate: {train_metrics['top5_hit_rate']:.1%}")
    print(f"  Mean Spearman ρ: {train_metrics['spearman_mean']:.3f}")

    # Feature importance
    print("\nFeature importance (gain):")
    importance = model.get_score(importance_type="gain")
    # Map feature indices back to names
    feat_names = TIER_C_FEATURES
    importance_named = {}
    for k, v in importance.items():
        idx = int(k.lstrip("f"))
        if idx < len(feat_names):
            importance_named[feat_names[idx]] = v
    for name, gain in sorted(importance_named.items(), key=lambda x: -x[1])[:15]:
        print(f"  {name:40}  gain={gain:.4f}")

    # Save scores
    out_df = df_with_scores[["season_id", "award_year", "player_name_raw", "rank", "tier_c_score"]].copy()
    table = pa.Table.from_pandas(out_df, preserve_index=False)
    pq.write_table(table, OUTPUT_PARQUET, compression="snappy")
    print(f"\n✅ Wrote {OUTPUT_PARQUET}")

    # Save model
    model_json = {
        "model_path": str(MODEL_JSON),
        "feature_order": TIER_C_FEATURES,
        "params": params,
        "num_boost_round": 200,
        "training_metrics": {
            "top1_accuracy": train_metrics["top1_accuracy"],
            "top3_hit_rate": train_metrics["top3_hit_rate"],
            "top5_hit_rate": train_metrics["top5_hit_rate"],
            "spearman_mean": train_metrics["spearman_mean"],
            "total_years": train_metrics["total_years"],
        },
        "feature_importance_gain": importance_named,
        "held_out_seasons": sorted(list(HELD_OUT_SEASONS)),
    }
    METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(MODEL_JSON)
    print(f"✅ Wrote {MODEL_JSON}")
    with open(METRICS_JSON, "w") as f:
        json.dump(model_json, f, indent=2)
    print(f"✅ Wrote {METRICS_JSON}")

    print()
    print("=" * 70)
    print("Tier C summary:")
    print(f"  Training top-1 accuracy: {train_metrics['top1_accuracy']:.1%}")
    print(f"  Training top-3 hit rate: {train_metrics['top3_hit_rate']:.1%}")
    print(f"  Training top-5 hit rate: {train_metrics['top5_hit_rate']:.1%}")
    print(f"  Training mean Spearman ρ: {train_metrics['spearman_mean']:.3f}")
    print("=" * 70)
    print()
    print("Comparison vs Tier B (training data, sanity check):")
    print("  Tier B: top-1=33.9%, top-3=51.6%, top-5=69.4%, Spearman=0.428")
    print(f"  Tier C: top-1={train_metrics['top1_accuracy']:.1%}, top-3={train_metrics['top3_hit_rate']:.1%}, top-5={train_metrics['top5_hit_rate']:.1%}, Spearman={train_metrics['spearman_mean']:.3f}")


if __name__ == "__main__":
    main()
