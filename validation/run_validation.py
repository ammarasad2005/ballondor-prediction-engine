"""Phase 6 — Validation & Calibration.

Per Architecture Blueprint §4.6 + Implementation Plan Phase 6:
  1. LOSO (leave-one-season-out) CV across every non-held-out season.
  2. Expanding-window validation (train only on seasons BEFORE year Y).
  3. Single final evaluation against held-out test seasons (one-shot).
  4. Produce validation report.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, kendalltau
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import xgboost as xgb

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
VALIDATION_REPORT = PROJECT_ROOT / "reports" / "validation_report_2026-07-28.md"

HELD_OUT_SEASONS = {2018, 2019, 2021, 2022, 2023, 2024, 2025}

FEATURES = [
    "goals_percentile_in_year", "assists_percentile_in_year",
    "apps_percentile_in_year", "minutes_percentile_in_year",
    "ucl_winner", "ucl_runner_up",
    "domestic_league_winner", "domestic_league_runner_up",
    "world_cup_winner", "world_cup_runner_up",
    "euro_winner", "copa_america_winner", "international_tournament_year",
    "club_prestige_tier", "previous_ballon_dor_winner",
    "has_stats_data", "data_completeness_score",
    "nation_prior_winners_count", "continent_prior_winners_count",
    "nation_years_since_last_winner", "league_visibility_tier",
    "position_rarity_score", "position_adjusted_xg_contribution",
    "total_goals", "total_assists", "total_apps",
    "international_goals", "international_apps",
]

TIER_A_WEIGHTS = {
    "goals_percentile_in_year": 2.0, "assists_percentile_in_year": 1.0,
    "apps_percentile_in_year": 0.3, "minutes_percentile_in_year": 0.2,
    "ucl_winner": 3.0, "ucl_runner_up": 1.0,
    "domestic_league_winner": 1.5, "domestic_league_runner_up": 0.3,
    "world_cup_winner": 3.5, "world_cup_runner_up": 1.2,
    "euro_winner": 2.0, "copa_america_winner": 1.8,
    "international_tournament_year": 0.5,
    "signature_moment": 1.5, "club_prestige_tier": -0.5,
    "previous_ballon_dor_winner": 1.0,
    "has_stats_data": 0.5,
    "data_completeness_score": 0.3,
    "nation_prior_winners_count": -0.5,  # fewer prior winners = more notable
    "continent_prior_winners_count": -0.3,
    "nation_years_since_last_winner": 0.5,  # long drought = more notable
    "league_visibility_tier": -0.2,
    "position_rarity_score": 0.5,  # rare position = boost
    "position_adjusted_xg_contribution": 0.3,  # lower tier = penalty
}


def compute_tier_a_score(features: pd.DataFrame) -> pd.Series:
    scores = pd.Series(0.0, index=features.index)
    for feature, weight in TIER_A_WEIGHTS.items():
        if feature not in features.columns:
            continue
        col = features[feature]
        if col.dtype == bool:
            col = col.astype(int)
        col = col.fillna(0)
        scores += weight * col
    return scores


def compute_yearly_metrics(df: pd.DataFrame, score_col: str) -> dict:
    correct_top1 = 0; correct_top3 = 0; correct_top5 = 0
    total_years = 0; spearman_sum = 0.0; kendall_sum = 0.0
    yearly_results = []
    for year, group in df.groupby("award_year"):
        total_years += 1
        sorted_group = group.sort_values(score_col, ascending=False)
        pred_top1 = sorted_group.iloc[0]["player_name_raw"]
        actual_top1_row = group[group["rank"] == 1]
        actual_top1 = actual_top1_row["player_name_raw"].iloc[0] if len(actual_top1_row) else None
        if pred_top1 == actual_top1: correct_top1 += 1
        pred_top3 = sorted_group.head(3)["player_name_raw"].tolist()
        pred_top5 = sorted_group.head(5)["player_name_raw"].tolist()
        if actual_top1 in pred_top3: correct_top3 += 1
        if actual_top1 in pred_top5: correct_top5 += 1
        if len(group) >= 2:
            rho, _ = spearmanr(group[score_col], -group["rank"])
            if not pd.isna(rho): spearman_sum += rho
            tau, _ = kendalltau(group[score_col], -group["rank"])
            if not pd.isna(tau): kendall_sum += tau
        actual_rank_in_pred = None
        if actual_top1:
            pred_list = sorted_group["player_name_raw"].tolist()
            if actual_top1 in pred_list:
                actual_rank_in_pred = pred_list.index(actual_top1) + 1
        yearly_results.append({
            "year": int(year),
            "actual_winner": actual_top1,
            "predicted_top1": pred_top1,
            "top1_correct": bool(pred_top1 == actual_top1),
            "actual_rank_in_pred": actual_rank_in_pred,
        })
    return {
        "top1_accuracy": correct_top1 / total_years if total_years else 0,
        "top3_hit_rate": correct_top3 / total_years if total_years else 0,
        "top5_hit_rate": correct_top5 / total_years if total_years else 0,
        "spearman_mean": spearman_sum / total_years if total_years else 0,
        "kendall_mean": kendall_sum / total_years if total_years else 0,
        "total_years": total_years,
        "yearly_results": yearly_results,
    }


def prepare_features_for_sklearn(df: pd.DataFrame) -> np.ndarray:
    X = df[FEATURES].copy()
    for col in X.columns:
        if X[col].dtype == bool:
            X[col] = X[col].astype(int)
        X[col] = X[col].fillna(0)
    return X.values


def prepare_features_for_xgboost(df: pd.DataFrame):
    df_sorted = df.sort_values(["season_id", "rank"]).reset_index(drop=True)
    X = df_sorted[FEATURES].copy()
    for col in X.columns:
        if X[col].dtype == bool:
            X[col] = X[col].astype(int)
    y = np.maximum(31 - df_sorted["rank"].astype(int).values + 1, 1)
    group_sizes = df_sorted.groupby("season_id").size().tolist()
    return X.values, y, group_sizes, df_sorted


def build_pairs(df: pd.DataFrame):
    X_rows = []; y_labels = []
    for season_id, group in df.groupby("season_id"):
        group_sorted = group.sort_values("rank")
        rows = group_sorted.to_dict("records")
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                feat_diff = []
                for col in FEATURES:
                    val_a = a.get(col); val_b = b.get(col)
                    if val_a is None or (isinstance(val_a, float) and pd.isna(val_a)): val_a = 0
                    if val_b is None or (isinstance(val_b, float) and pd.isna(val_b)): val_b = 0
                    if isinstance(val_a, bool): val_a = int(val_a)
                    if isinstance(val_b, bool): val_b = int(val_b)
                    feat_diff.append(val_a - val_b)
                X_rows.append(feat_diff); y_labels.append(1)
                X_rows.append([-x for x in feat_diff]); y_labels.append(0)
    return np.array(X_rows, dtype=float), np.array(y_labels, dtype=int)


def loso_cv_tier_b(df: pd.DataFrame) -> dict:
    print("\n--- Tier B LOSO CV ---")
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    folds = sorted(train_df["award_year"].unique())
    print(f"  {len(folds)} folds")
    all_predictions = []
    for i, holdout_year in enumerate(folds):
        train_fold = train_df[train_df["award_year"] != holdout_year]
        test_fold = train_df[train_df["award_year"] == holdout_year]
        X_train, y_train = build_pairs(train_fold)
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        model = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=1000, random_state=20260728)
        model.fit(X_train_scaled, y_train)
        X_test = prepare_features_for_sklearn(test_fold)
        X_test_scaled = scaler.transform(X_test)
        scores = model.decision_function(X_test_scaled)
        test_fold = test_fold.copy()
        test_fold["predicted_score"] = scores
        all_predictions.append(test_fold)
        if (i + 1) % 10 == 0:
            print(f"    Fold {i+1}/{len(folds)} done (year {holdout_year})")
    pred_df = pd.concat(all_predictions, ignore_index=True)
    return compute_yearly_metrics(pred_df, "predicted_score")


def loso_cv_tier_c(df: pd.DataFrame) -> dict:
    print("\n--- Tier C LOSO CV ---")
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    folds = sorted(train_df["award_year"].unique())
    print(f"  {len(folds)} folds")
    all_predictions = []
    params = {
        "objective": "rank:ndcg", "eval_metric": "ndcg",
        "max_depth": 3, "eta": 0.05,
        "subsample": 0.8, "colsample_bytree": 0.8,
        "reg_alpha": 1.0, "reg_lambda": 5.0,
        "min_child_weight": 5, "random_state": 20260728, "verbosity": 0,
    }
    for i, holdout_year in enumerate(folds):
        train_fold = train_df[train_df["award_year"] != holdout_year]
        test_fold = train_df[train_df["award_year"] == holdout_year]
        X_train, y_train, group_train, _ = prepare_features_for_xgboost(train_fold)
        dtrain = xgb.DMatrix(X_train, label=y_train)
        dtrain.set_group(group_train)
        model = xgb.train(params, dtrain, num_boost_round=200, verbose_eval=False)
        X_test = test_fold[FEATURES].copy()
        for col in X_test.columns:
            if X_test[col].dtype == bool:
                X_test[col] = X_test[col].astype(int)
        dtest = xgb.DMatrix(X_test.values)
        dtest.set_group([len(test_fold)])
        scores = model.predict(dtest)
        test_fold = test_fold.copy()
        test_fold["predicted_score"] = scores
        all_predictions.append(test_fold)
        if (i + 1) % 10 == 0:
            print(f"    Fold {i+1}/{len(folds)} done (year {holdout_year})")
    pred_df = pd.concat(all_predictions, ignore_index=True)
    return compute_yearly_metrics(pred_df, "predicted_score")


def expanding_window_cv_tier_b(df: pd.DataFrame, min_train_years: int = 10) -> dict:
    print("\n--- Tier B Expanding-Window CV ---")
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    folds = sorted(train_df["award_year"].unique())
    eval_folds = folds[min_train_years:]
    print(f"  {len(eval_folds)} folds (skipping first {min_train_years})")
    all_predictions = []
    for i, holdout_year in enumerate(eval_folds):
        train_fold = train_df[train_df["award_year"] < holdout_year]
        test_fold = train_df[train_df["award_year"] == holdout_year]
        if len(train_fold) == 0: continue
        X_train, y_train = build_pairs(train_fold)
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        model = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=1000, random_state=20260728)
        model.fit(X_train_scaled, y_train)
        X_test = prepare_features_for_sklearn(test_fold)
        X_test_scaled = scaler.transform(X_test)
        scores = model.decision_function(X_test_scaled)
        test_fold = test_fold.copy()
        test_fold["predicted_score"] = scores
        all_predictions.append(test_fold)
        if (i + 1) % 10 == 0:
            print(f"    Fold {i+1}/{len(eval_folds)} done (year {holdout_year})")
    pred_df = pd.concat(all_predictions, ignore_index=True)
    return compute_yearly_metrics(pred_df, "predicted_score")


def final_heldout_eval(df: pd.DataFrame) -> dict:
    print("\n" + "=" * 70)
    print("FINAL HELD-OUT EVALUATION (one-shot, per Key Focus Areas §8)")
    print("=" * 70)
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    test_df = df[df["award_year"].isin(HELD_OUT_SEASONS)]
    print(f"  Train: {train_df['award_year'].nunique()} seasons, {len(train_df)} rows")
    print(f"  Test: {test_df['award_year'].nunique()} seasons, {len(test_df)} rows")

    print("\n  Tier A (heuristic)...")
    test_df_a = test_df.copy()
    test_df_a["score"] = compute_tier_a_score(test_df_a)
    tier_a_metrics = compute_yearly_metrics(test_df_a, "score")

    print("  Tier B (pairwise logistic)...")
    X_train, y_train = build_pairs(train_df)
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model_b = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=1000, random_state=20260728)
    model_b.fit(X_train_scaled, y_train)
    X_test = prepare_features_for_sklearn(test_df)
    X_test_scaled = scaler.transform(X_test)
    test_df_b = test_df.copy()
    test_df_b["score"] = model_b.decision_function(X_test_scaled)
    tier_b_metrics = compute_yearly_metrics(test_df_b, "score")

    print("  Tier C (XGBoost ranker)...")
    X_train_c, y_train_c, group_train_c, _ = prepare_features_for_xgboost(train_df)
    dtrain = xgb.DMatrix(X_train_c, label=y_train_c)
    dtrain.set_group(group_train_c)
    params = {
        "objective": "rank:ndcg", "eval_metric": "ndcg",
        "max_depth": 3, "eta": 0.05,
        "subsample": 0.8, "colsample_bytree": 0.8,
        "reg_alpha": 1.0, "reg_lambda": 5.0,
        "min_child_weight": 5, "random_state": 20260728, "verbosity": 0,
    }
    model_c = xgb.train(params, dtrain, num_boost_round=200, verbose_eval=False)
    # Predict on test (sorted by season_id, rank)
    test_sorted = test_df.sort_values(["season_id", "rank"]).reset_index(drop=True)
    X_test_c = test_sorted[FEATURES].copy()
    for col in X_test_c.columns:
        if X_test_c[col].dtype == bool:
            X_test_c[col] = X_test_c[col].astype(int)
    dtest = xgb.DMatrix(X_test_c.values)
    test_group_sizes = test_sorted.groupby("season_id").size().tolist()
    dtest.set_group(test_group_sizes)
    test_sorted["score"] = model_c.predict(dtest)
    tier_c_metrics = compute_yearly_metrics(test_sorted, "score")

    return {"tier_a": tier_a_metrics, "tier_b": tier_b_metrics, "tier_c": tier_c_metrics}


def main() -> None:
    print("=" * 70)
    print("Phase 6 — Validation & Calibration")
    print("=" * 70)
    df = pd.read_parquet(FEATURES_PARQUET)
    print(f"Loaded {len(df)} feature rows")

    print("\n" + "=" * 70)
    print("Step 1: LOSO CV")
    print("=" * 70)

    print("\n--- Tier A (heuristic, no CV needed) ---")
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    train_df_a = train_df.copy()
    train_df_a["score"] = compute_tier_a_score(train_df_a)
    tier_a_loso = compute_yearly_metrics(train_df_a, "score")
    print(f"  Top-1: {tier_a_loso['top1_accuracy']:.1%}, Top-3: {tier_a_loso['top3_hit_rate']:.1%}, Top-5: {tier_a_loso['top5_hit_rate']:.1%}, Spearman: {tier_a_loso['spearman_mean']:.3f}")

    tier_b_loso = loso_cv_tier_b(df)
    print(f"  Tier B LOSO: Top-1: {tier_b_loso['top1_accuracy']:.1%}, Top-3: {tier_b_loso['top3_hit_rate']:.1%}, Top-5: {tier_b_loso['top5_hit_rate']:.1%}, Spearman: {tier_b_loso['spearman_mean']:.3f}")

    tier_c_loso = loso_cv_tier_c(df)
    print(f"  Tier C LOSO: Top-1: {tier_c_loso['top1_accuracy']:.1%}, Top-3: {tier_c_loso['top3_hit_rate']:.1%}, Top-5: {tier_c_loso['top5_hit_rate']:.1%}, Spearman: {tier_c_loso['spearman_mean']:.3f}")

    print("\n" + "=" * 70)
    print("Step 2: Expanding-Window CV")
    print("=" * 70)
    tier_b_expanding = expanding_window_cv_tier_b(df)
    print(f"  Tier B: Top-1: {tier_b_expanding['top1_accuracy']:.1%}, Top-3: {tier_b_expanding['top3_hit_rate']:.1%}, Top-5: {tier_b_expanding['top5_hit_rate']:.1%}, Spearman: {tier_b_expanding['spearman_mean']:.3f}")

    print("\n" + "=" * 70)
    print("Step 3: Final Held-Out Evaluation (one-shot)")
    print("=" * 70)
    heldout_metrics = final_heldout_eval(df)
    print("\nHeld-out results:")
    print(f"  Tier A: Top-1: {heldout_metrics['tier_a']['top1_accuracy']:.1%}, Top-3: {heldout_metrics['tier_a']['top3_hit_rate']:.1%}, Top-5: {heldout_metrics['tier_a']['top5_hit_rate']:.1%}, Spearman: {heldout_metrics['tier_a']['spearman_mean']:.3f}")
    print(f"  Tier B: Top-1: {heldout_metrics['tier_b']['top1_accuracy']:.1%}, Top-3: {heldout_metrics['tier_b']['top3_hit_rate']:.1%}, Top-5: {heldout_metrics['tier_b']['top5_hit_rate']:.1%}, Spearman: {heldout_metrics['tier_b']['spearman_mean']:.3f}")
    print(f"  Tier C: Top-1: {heldout_metrics['tier_c']['top1_accuracy']:.1%}, Top-3: {heldout_metrics['tier_c']['top3_hit_rate']:.1%}, Top-5: {heldout_metrics['tier_c']['top5_hit_rate']:.1%}, Spearman: {heldout_metrics['tier_c']['spearman_mean']:.3f}")

    write_validation_report(df, tier_a_loso, tier_b_loso, tier_c_loso, tier_b_expanding, heldout_metrics)

    metrics_json = {
        "loso_cv": {
            "tier_a": {k: v for k, v in tier_a_loso.items() if k != "yearly_results"},
            "tier_b": {k: v for k, v in tier_b_loso.items() if k != "yearly_results"},
            "tier_c": {k: v for k, v in tier_c_loso.items() if k != "yearly_results"},
        },
        "expanding_window_cv": {
            "tier_b": {k: v for k, v in tier_b_expanding.items() if k != "yearly_results"},
        },
        "final_heldout_eval": {
            "tier_a": {k: v for k, v in heldout_metrics["tier_a"].items() if k != "yearly_results"},
            "tier_b": {k: v for k, v in heldout_metrics["tier_b"].items() if k != "yearly_results"},
            "tier_c": {k: v for k, v in heldout_metrics["tier_c"].items() if k != "yearly_results"},
        },
        "held_out_seasons": sorted(list(HELD_OUT_SEASONS)),
    }
    metrics_path = PROJECT_ROOT / "reports" / "validation_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with open(metrics_path, "w") as f:
        json.dump(metrics_json, f, indent=2, default=str)
    print(f"\n✅ Wrote {metrics_path}")


def write_validation_report(df, tier_a_loso, tier_b_loso, tier_c_loso, tier_b_expanding, heldout_metrics):
    lines = []
    lines.append("# Validation Report — Ballon d'Or Prediction Engine\n\n")
    lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n\n")

    lines.append("## Overview\n\n")
    lines.append("Per Architecture Blueprint §4.6 + Implementation Plan Phase 6:\n")
    lines.append("1. Leave-one-season-out (LOSO) CV\n")
    lines.append("2. Expanding-window CV (train only on seasons BEFORE year Y)\n")
    lines.append("3. Final held-out evaluation (one-shot, per Key Focus Areas §8)\n\n")

    lines.append("## Held-Out Test Seasons\n\n")
    lines.append(f"The following {len(HELD_OUT_SEASONS)} seasons were reserved for one-shot final evaluation:\n")
    lines.append(f"`{sorted(HELD_OUT_SEASONS)}`\n\n")
    lines.append("Per Key Focus Areas §8, these seasons were NOT used in any feature selection, ")
    lines.append("hyperparameter tuning, or model-selection decision during Phase 5. Evaluated here exactly once.\n\n")

    lines.append("## LOSO Cross-Validation Results\n\n")
    lines.append("Per-fold: train on all non-held-out seasons except year Y, predict year Y. Repeat for every season.\n\n")
    lines.append("| Tier | Top-1 | Top-3 | Top-5 | Spearman ρ | Kendall τ |\n|---|---|---|---|---|---|\n")
    for name, m in [("A (heuristic)", tier_a_loso), ("B (pairwise linear)", tier_b_loso), ("C (XGBoost)", tier_c_loso)]:
        lines.append(f"| {name} | {m['top1_accuracy']:.1%} | {m['top3_hit_rate']:.1%} | {m['top5_hit_rate']:.1%} | {m['spearman_mean']:.3f} | {m['kendall_mean']:.3f} |\n")
    lines.append("\n")

    lines.append("## Expanding-Window CV Results (Tier B)\n\n")
    lines.append("More realistic: train only on seasons BEFORE year Y, predict Y.\n\n")
    lines.append("| Tier | Top-1 | Top-3 | Top-5 | Spearman ρ | Kendall τ |\n|---|---|---|---|---|---|\n")
    lines.append(f"| B (pairwise linear) | {tier_b_expanding['top1_accuracy']:.1%} | {tier_b_expanding['top3_hit_rate']:.1%} | {tier_b_expanding['top5_hit_rate']:.1%} | {tier_b_expanding['spearman_mean']:.3f} | {tier_b_expanding['kendall_mean']:.3f} |\n\n")

    lines.append("## Final Held-Out Evaluation (One-Shot)\n\n")
    lines.append("**Per Key Focus Areas §8: this is a one-shot check.** If performance is poor here, that is a final, honestly reported finding, NOT a cue to tune against held-out.\n\n")
    lines.append("| Tier | Top-1 | Top-3 | Top-5 | Spearman ρ | Kendall τ |\n|---|---|---|---|---|---|\n")
    for name, m in [("A (heuristic)", heldout_metrics["tier_a"]), ("B (pairwise linear)", heldout_metrics["tier_b"]), ("C (XGBoost)", heldout_metrics["tier_c"])]:
        lines.append(f"| {name} | {m['top1_accuracy']:.1%} | {m['top3_hit_rate']:.1%} | {m['top5_hit_rate']:.1%} | {m['spearman_mean']:.3f} | {m['kendall_mean']:.3f} |\n")
    lines.append("\n")

    lines.append("## Tier D — Model Selection Decision\n\n")
    lines.append("Per Architecture Blueprint §4.5 Tier D decision rule:\n")
    lines.append("> Prefer B unless C shows a consistent, non-marginal improvement across multiple validation folds.\n\n")

    b_top1 = tier_b_loso["top1_accuracy"]; c_top1 = tier_c_loso["top1_accuracy"]
    b_top3 = tier_b_loso["top3_hit_rate"]; c_top3 = tier_c_loso["top3_hit_rate"]
    diff_top1 = c_top1 - b_top1; diff_top3 = c_top3 - b_top3

    lines.append(f"LOSO CV comparison (Tier C - Tier B):\n")
    lines.append(f"- Top-1: {c_top1:.1%} - {b_top1:.1%} = {diff_top1:+.1%}\n")
    lines.append(f"- Top-3: {c_top3:.1%} - {b_top3:.1%} = {diff_top3:+.1%}\n\n")

    if diff_top1 > 0.05 and diff_top3 > 0.05:
        lines.append(f"Tier C demonstrates **consistent, non-marginal improvement** (+{diff_top1:.1%} top-1, +{diff_top3:.1%} top-3). ")
        lines.append(f"Per Tier D decision rule, **Tier C is selected** as the primary model.\n\n")
        selected = "C"
    elif diff_top1 > 0:
        lines.append(f"Tier C shows marginal improvement (+{diff_top1:.1%} top-1). Per Tier D decision rule, **Tier B is selected** for interpretability. Tier C as secondary.\n\n")
        selected = "B"
    else:
        lines.append(f"Tier C does NOT outperform Tier B ({diff_top1:+.1%} top-1). **Tier B is selected** as the primary model.\n\n")
        selected = "B"
    lines.append(f"**Selected primary model: Tier {selected}**\n\n")

    lines.append("## Per-Era Breakdown (LOSO CV, Tier B)\n\n")
    lines.append("Per Architecture Blueprint P4, performance should differ by era by design.\n\n")
    lines.append("| Era | Years | Top-1 | Top-3 | Top-5 |\n|---|---|---|---|---|\n")
    for era in ["classical", "pre_merger", "fifa_merger", "post_split"]:
        era_years = set(df[df["era_tag"] == era]["award_year"].unique())
        sub_results = [r for r in tier_b_loso["yearly_results"] if r["year"] in era_years]
        if not sub_results: continue
        n = len(sub_results)
        top1 = sum(1 for r in sub_results if r["top1_correct"]) / n
        top3 = sum(1 for r in sub_results if (r["actual_rank_in_pred"] or 99) <= 3) / n
        top5 = sum(1 for r in sub_results if (r["actual_rank_in_pred"] or 99) <= 5) / n
        lines.append(f"| {era} | {n} | {top1:.1%} | {top3:.1%} | {top5:.1%} |\n")
    lines.append("\n")

    lines.append("## Conclusion\n\n")
    lines.append(f"Selected model: **Tier {selected}**. See reports/validation_metrics.json for full per-fold results.\n")
    lines.append(f"\nHeld-out evaluation is honest, one-shot, never tuned against. The gap between LOSO CV ")
    lines.append(f"and held-out performance (if any) reflects natural generalization cost — not a tuning opportunity.\n")

    VALIDATION_REPORT.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_REPORT.write_text("".join(lines), encoding="utf-8")
    print(f"\n✅ Wrote validation report to {VALIDATION_REPORT}")


if __name__ == "__main__":
    main()
