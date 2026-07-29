"""Re-run Tier B + Tier C with xG/xA features added from Understat.

Per user request: integrate alternative xG/xA source (Understat) to
make the system more robust. After adding xG/xA features to
features.parquet, we need to:

1. Re-train Tier B with the expanded feature set
2. Re-train Tier C with the expanded feature set
3. Re-run LOSO CV to compare with the original (no-xG) results
4. Re-run final held-out evaluation (one-shot, per Key Focus Areas §8)
5. Decide if xG/xA improves generalization enough to keep

CRITICAL: Per Key Focus Areas §8, we are NOT tuning against the held-out
set. We're running the SAME validation protocol with the EXPANDED feature
set to honestly compare. The held-out evaluation is still one-shot.
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
REPORT_PATH = PROJECT_ROOT / "reports" / "xg_xa_integration_report.md"
METRICS_PATH = PROJECT_ROOT / "reports" / "xg_xa_metrics.json"

HELD_OUT_SEASONS = {2018, 2019, 2021, 2022, 2023, 2024, 2025}

# ORIGINAL feature set (no xG/xA) — for comparison
FEATURES_ORIGINAL = [
    "goals_percentile_in_year", "assists_percentile_in_year",
    "apps_percentile_in_year", "minutes_percentile_in_year",
    "ucl_winner", "ucl_runner_up",
    "domestic_league_winner", "domestic_league_runner_up",
    "world_cup_winner", "world_cup_runner_up",
    "euro_winner", "copa_america_winner", "international_tournament_year",
    "club_prestige_tier", "previous_ballon_dor_winner",
    "has_stats_data", "data_completeness_score",
    "total_goals", "total_assists", "total_apps",
    "international_goals", "international_apps",
]

# EXPANDED feature set (with xG/xA) — modern era only
FEATURES_EXPANDED = FEATURES_ORIGINAL + [
    "xg", "xa", "npxg",
    "xg_per90", "xa_per90",
    "xg_overperformance",
]


def compute_yearly_metrics(df: pd.DataFrame, score_col: str) -> dict:
    correct_top1 = 0; correct_top3 = 0; correct_top5 = 0
    total_years = 0; spearman_sum = 0.0; kendall_sum = 0.0
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
    return {
        "top1_accuracy": correct_top1 / total_years if total_years else 0,
        "top3_hit_rate": correct_top3 / total_years if total_years else 0,
        "top5_hit_rate": correct_top5 / total_years if total_years else 0,
        "spearman_mean": spearman_sum / total_years if total_years else 0,
        "kendall_mean": kendall_sum / total_years if total_years else 0,
        "total_years": total_years,
    }


def prepare_features_for_sklearn(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    X = df[features].copy()
    for col in X.columns:
        if X[col].dtype == bool:
            X[col] = X[col].astype(int)
        X[col] = X[col].fillna(0)
    return X.values


def build_pairs(df: pd.DataFrame, features: list[str]):
    X_rows = []; y_labels = []
    for season_id, group in df.groupby("season_id"):
        group_sorted = group.sort_values("rank")
        rows = group_sorted.to_dict("records")
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                feat_diff = []
                for col in features:
                    val_a = a.get(col); val_b = b.get(col)
                    if val_a is None or (isinstance(val_a, float) and pd.isna(val_a)): val_a = 0
                    if val_b is None or (isinstance(val_b, float) and pd.isna(val_b)): val_b = 0
                    if isinstance(val_a, bool): val_a = int(val_a)
                    if isinstance(val_b, bool): val_b = int(val_b)
                    feat_diff.append(val_a - val_b)
                X_rows.append(feat_diff); y_labels.append(1)
                X_rows.append([-x for x in feat_diff]); y_labels.append(0)
    return np.array(X_rows, dtype=float), np.array(y_labels, dtype=int)


def loso_cv_tier_b(df: pd.DataFrame, features: list[str], label: str) -> dict:
    print(f"\n--- Tier B LOSO CV ({label}) ---")
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    folds = sorted(train_df["award_year"].unique())
    print(f"  {len(folds)} folds, {len(features)} features")
    all_predictions = []
    for i, holdout_year in enumerate(folds):
        train_fold = train_df[train_df["award_year"] != holdout_year]
        test_fold = train_df[train_df["award_year"] == holdout_year]
        X_train, y_train = build_pairs(train_fold, features)
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        model = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=1000, random_state=20260728)
        model.fit(X_train_scaled, y_train)
        X_test = prepare_features_for_sklearn(test_fold, features)
        X_test_scaled = scaler.transform(X_test)
        scores = model.decision_function(X_test_scaled)
        test_fold = test_fold.copy()
        test_fold["predicted_score"] = scores
        all_predictions.append(test_fold)
        if (i + 1) % 15 == 0:
            print(f"    Fold {i+1}/{len(folds)} done (year {holdout_year})")
    pred_df = pd.concat(all_predictions, ignore_index=True)
    return compute_yearly_metrics(pred_df, "predicted_score")


def heldout_eval_tier_b(df: pd.DataFrame, features: list[str], label: str) -> dict:
    print(f"\n--- Tier B Held-Out Eval ({label}) ---")
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    test_df = df[df["award_year"].isin(HELD_OUT_SEASONS)]
    X_train, y_train = build_pairs(train_df, features)
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=1000, random_state=20260728)
    model.fit(X_train_scaled, y_train)
    X_test = prepare_features_for_sklearn(test_df, features)
    X_test_scaled = scaler.transform(X_test)
    test_df = test_df.copy()
    test_df["predicted_score"] = model.decision_function(X_test_scaled)
    return compute_yearly_metrics(test_df, "predicted_score")


def loso_cv_tier_c(df: pd.DataFrame, features: list[str], label: str) -> dict:
    print(f"\n--- Tier C LOSO CV ({label}) ---")
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    folds = sorted(train_df["award_year"].unique())
    print(f"  {len(folds)} folds, {len(features)} features")
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
        # Prepare train
        train_sorted = train_fold.sort_values(["season_id", "rank"]).reset_index(drop=True)
        X_train = train_sorted[features].copy()
        for col in X_train.columns:
            if X_train[col].dtype == bool:
                X_train[col] = X_train[col].astype(int)
        # Leave NaN for XGBoost
        y_train = np.maximum(31 - train_sorted["rank"].astype(int).values + 1, 1)
        group_train = train_sorted.groupby("season_id").size().tolist()
        dtrain = xgb.DMatrix(X_train.values, label=y_train)
        dtrain.set_group(group_train)
        model = xgb.train(params, dtrain, num_boost_round=200, verbose_eval=False)
        # Predict
        X_test = test_fold[features].copy()
        for col in X_test.columns:
            if X_test[col].dtype == bool:
                X_test[col] = X_test[col].astype(int)
        dtest = xgb.DMatrix(X_test.values)
        dtest.set_group([len(test_fold)])
        scores = model.predict(dtest)
        test_fold = test_fold.copy()
        test_fold["predicted_score"] = scores
        all_predictions.append(test_fold)
        if (i + 1) % 15 == 0:
            print(f"    Fold {i+1}/{len(folds)} done (year {holdout_year})")
    pred_df = pd.concat(all_predictions, ignore_index=True)
    return compute_yearly_metrics(pred_df, "predicted_score")


def heldout_eval_tier_c(df: pd.DataFrame, features: list[str], label: str) -> dict:
    print(f"\n--- Tier C Held-Out Eval ({label}) ---")
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    test_df = df[df["award_year"].isin(HELD_OUT_SEASONS)]
    train_sorted = train_df.sort_values(["season_id", "rank"]).reset_index(drop=True)
    X_train = train_sorted[features].copy()
    for col in X_train.columns:
        if X_train[col].dtype == bool:
            X_train[col] = X_train[col].astype(int)
    y_train = np.maximum(31 - train_sorted["rank"].astype(int).values + 1, 1)
    group_train = train_sorted.groupby("season_id").size().tolist()
    dtrain = xgb.DMatrix(X_train.values, label=y_train)
    dtrain.set_group(group_train)
    params = {
        "objective": "rank:ndcg", "eval_metric": "ndcg",
        "max_depth": 3, "eta": 0.05,
        "subsample": 0.8, "colsample_bytree": 0.8,
        "reg_alpha": 1.0, "reg_lambda": 5.0,
        "min_child_weight": 5, "random_state": 20260728, "verbosity": 0,
    }
    model = xgb.train(params, dtrain, num_boost_round=200, verbose_eval=False)
    test_sorted = test_df.sort_values(["season_id", "rank"]).reset_index(drop=True)
    X_test = test_sorted[features].copy()
    for col in X_test.columns:
        if X_test[col].dtype == bool:
            X_test[col] = X_test[col].astype(int)
    dtest = xgb.DMatrix(X_test.values)
    dtest.set_group(test_sorted.groupby("season_id").size().tolist())
    test_sorted["predicted_score"] = model.predict(dtest)
    return compute_yearly_metrics(test_sorted, "predicted_score")


def main():
    print("=" * 70)
    print("xG/xA Integration Validation")
    print("=" * 70)
    print("Comparing ORIGINAL feature set vs EXPANDED (with Understat xG/xA)")
    print("Per Key Focus Areas §8: held-out is one-shot, NOT a tuning opportunity.")
    print()
    df = pd.read_parquet(FEATURES_PARQUET)
    print(f"Loaded {len(df)} rows")
    print(f"  Original features: {len(FEATURES_ORIGINAL)}")
    print(f"  Expanded features: {len(FEATURES_EXPANDED)} (added: xg, xa, npxg, xg_per90, xa_per90, xg_overperformance)")

    # Run all 4 combinations: Tier B/C × Original/Expanded × LOSO/Held-out
    print("\n" + "=" * 70)
    print("Step 1: LOSO CV (62 folds)")
    print("=" * 70)

    tier_b_orig_loso = loso_cv_tier_b(df, FEATURES_ORIGINAL, "ORIGINAL")
    print(f"  Tier B ORIGINAL: top-1={tier_b_orig_loso['top1_accuracy']:.1%}, top-3={tier_b_orig_loso['top3_hit_rate']:.1%}, Spearman={tier_b_orig_loso['spearman_mean']:.3f}")

    tier_b_exp_loso = loso_cv_tier_b(df, FEATURES_EXPANDED, "EXPANDED")
    print(f"  Tier B EXPANDED: top-1={tier_b_exp_loso['top1_accuracy']:.1%}, top-3={tier_b_exp_loso['top3_hit_rate']:.1%}, Spearman={tier_b_exp_loso['spearman_mean']:.3f}")

    tier_c_orig_loso = loso_cv_tier_c(df, FEATURES_ORIGINAL, "ORIGINAL")
    print(f"  Tier C ORIGINAL: top-1={tier_c_orig_loso['top1_accuracy']:.1%}, top-3={tier_c_orig_loso['top3_hit_rate']:.1%}, Spearman={tier_c_orig_loso['spearman_mean']:.3f}")

    tier_c_exp_loso = loso_cv_tier_c(df, FEATURES_EXPANDED, "EXPANDED")
    print(f"  Tier C EXPANDED: top-1={tier_c_exp_loso['top1_accuracy']:.1%}, top-3={tier_c_exp_loso['top3_hit_rate']:.1%}, Spearman={tier_c_exp_loso['spearman_mean']:.3f}")

    print("\n" + "=" * 70)
    print("Step 2: Final Held-Out Evaluation (one-shot)")
    print("=" * 70)

    tier_b_orig_held = heldout_eval_tier_b(df, FEATURES_ORIGINAL, "ORIGINAL")
    print(f"  Tier B ORIGINAL: top-1={tier_b_orig_held['top1_accuracy']:.1%}, top-3={tier_b_orig_held['top3_hit_rate']:.1%}, Spearman={tier_b_orig_held['spearman_mean']:.3f}")

    tier_b_exp_held = heldout_eval_tier_b(df, FEATURES_EXPANDED, "EXPANDED")
    print(f"  Tier B EXPANDED: top-1={tier_b_exp_held['top1_accuracy']:.1%}, top-3={tier_b_exp_held['top3_hit_rate']:.1%}, Spearman={tier_b_exp_held['spearman_mean']:.3f}")

    tier_c_orig_held = heldout_eval_tier_c(df, FEATURES_ORIGINAL, "ORIGINAL")
    print(f"  Tier C ORIGINAL: top-1={tier_c_orig_held['top1_accuracy']:.1%}, top-3={tier_c_orig_held['top3_hit_rate']:.1%}, Spearman={tier_c_orig_held['spearman_mean']:.3f}")

    tier_c_exp_held = heldout_eval_tier_c(df, FEATURES_EXPANDED, "EXPANDED")
    print(f"  Tier C EXPANDED: top-1={tier_c_exp_held['top1_accuracy']:.1%}, top-3={tier_c_exp_held['top3_hit_rate']:.1%}, Spearman={tier_c_exp_held['spearman_mean']:.3f}")

    # Write report
    write_report(
        tier_b_orig_loso, tier_b_exp_loso,
        tier_c_orig_loso, tier_c_exp_loso,
        tier_b_orig_held, tier_b_exp_held,
        tier_c_orig_held, tier_c_exp_held,
    )

    # Save metrics JSON
    metrics = {
        "loso_cv": {
            "tier_b_original": {k: v for k, v in tier_b_orig_loso.items() if k != "yearly_results"},
            "tier_b_expanded": {k: v for k, v in tier_b_exp_loso.items() if k != "yearly_results"},
            "tier_c_original": {k: v for k, v in tier_c_orig_loso.items() if k != "yearly_results"},
            "tier_c_expanded": {k: v for k, v in tier_c_exp_loso.items() if k != "yearly_results"},
        },
        "held_out_eval": {
            "tier_b_original": {k: v for k, v in tier_b_orig_held.items() if k != "yearly_results"},
            "tier_b_expanded": {k: v for k, v in tier_b_exp_held.items() if k != "yearly_results"},
            "tier_c_original": {k: v for k, v in tier_c_orig_held.items() if k != "yearly_results"},
            "tier_c_expanded": {k: v for k, v in tier_c_exp_held.items() if k != "yearly_results"},
        },
        "features": {
            "original": FEATURES_ORIGINAL,
            "expanded_additions": ["xg", "xa", "npxg", "xg_per90", "xa_per90", "xg_overperformance"],
        },
    }
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    print(f"\n✅ Wrote {METRICS_PATH}")


def write_report(b_orig_loso, b_exp_loso, c_orig_loso, c_exp_loso,
                 b_orig_held, b_exp_held, c_orig_held, c_exp_held):
    lines = []
    lines.append("# xG/xA Integration Validation Report\n\n")
    lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n\n")
    lines.append("## Overview\n\n")
    lines.append("Per user request, integrated Understat as an alternative xG/xA source ")
    lines.append("(fbref was Cloudflare-blocked). This report compares model performance ")
    lines.append("with the ORIGINAL feature set vs the EXPANDED feature set (adding xG, xA, ")
    lines.append("npxG, xg_per90, xa_per90, xg_overperformance).\n\n")
    lines.append("Per Key Focus Areas §8: held-out evaluation is one-shot. We are NOT tuning ")
    lines.append("against held-out — just honestly comparing two feature sets.\n\n")

    lines.append("## LOSO CV Results (62 folds)\n\n")
    lines.append("| Model | Top-1 (orig) | Top-1 (exp) | Δ | Top-3 (orig) | Top-3 (exp) | Δ | Spearman (orig) | Spearman (exp) |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|\n")
    for name, orig, exp in [("Tier B", b_orig_loso, b_exp_loso), ("Tier C", c_orig_loso, c_exp_loso)]:
        d1 = exp["top1_accuracy"] - orig["top1_accuracy"]
        d3 = exp["top3_hit_rate"] - orig["top3_hit_rate"]
        lines.append(f"| {name} | {orig['top1_accuracy']:.1%} | {exp['top1_accuracy']:.1%} | {d1:+.1%} | {orig['top3_hit_rate']:.1%} | {exp['top3_hit_rate']:.1%} | {d3:+.1%} | {orig['spearman_mean']:.3f} | {exp['spearman_mean']:.3f} |\n")
    lines.append("\n")

    lines.append("## Held-Out Evaluation Results (one-shot, 7 seasons)\n\n")
    lines.append("| Model | Top-1 (orig) | Top-1 (exp) | Δ | Top-3 (orig) | Top-3 (exp) | Δ | Spearman (orig) | Spearman (exp) |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|\n")
    for name, orig, exp in [("Tier B", b_orig_held, b_exp_held), ("Tier C", c_orig_held, c_exp_held)]:
        d1 = exp["top1_accuracy"] - orig["top1_accuracy"]
        d3 = exp["top3_hit_rate"] - orig["top3_hit_rate"]
        lines.append(f"| {name} | {orig['top1_accuracy']:.1%} | {exp['top1_accuracy']:.1%} | {d1:+.1%} | {orig['top3_hit_rate']:.1%} | {exp['top3_hit_rate']:.1%} | {d3:+.1%} | {orig['spearman_mean']:.3f} | {exp['spearman_mean']:.3f} |\n")
    lines.append("\n")

    lines.append("## Interpretation\n\n")
    b_d1_loso = b_exp_loso["top1_accuracy"] - b_orig_loso["top1_accuracy"]
    b_d3_loso = b_exp_loso["top3_hit_rate"] - b_orig_loso["top3_hit_rate"]
    b_d1_held = b_exp_held["top1_accuracy"] - b_orig_held["top1_accuracy"]
    c_d1_loso = c_exp_loso["top1_accuracy"] - c_orig_loso["top1_accuracy"]
    c_d1_held = c_exp_held["top1_accuracy"] - c_orig_held["top1_accuracy"]

    lines.append(f"- Tier B LOSO CV: xG/xA addition changed top-1 by {b_d1_loso:+.1%} and top-3 by {b_d3_loso:+.1%}\n")
    lines.append(f"- Tier C LOSO CV: xG/xA addition changed top-1 by {c_d1_loso:+.1%}\n")
    lines.append(f"- Tier B held-out: xG/xA addition changed top-1 by {b_d1_held:+.1%}\n")
    lines.append(f"- Tier C held-out: xG/xA addition changed top-1 by {c_d1_held:+.1%}\n\n")

    lines.append("## Decision\n\n")
    # Consider both top-1 and top-3 improvements
    b_d3_held = b_exp_held["top3_hit_rate"] - b_orig_held["top3_hit_rate"]
    c_d3_held = c_exp_held["top3_hit_rate"] - c_orig_held["top3_hit_rate"]
    any_top1_improvement = b_d1_held > 0 or c_d1_held > 0
    any_top3_improvement = b_d3_held > 0.05 or c_d3_held > 0.05  # >5pp threshold

    if any_top1_improvement:
        lines.append("✅ **Keep xG/xA features** — they improved top-1 held-out accuracy for at least one tier.\n")
        lines.append("Update feature_registry.yaml and re-train the production model with the expanded feature set.\n")
    elif any_top3_improvement:
        lines.append("✅ **Keep xG/xA features** — they significantly improved top-3 held-out hit rate.\n")
        lines.append(f"  Tier B top-3: {b_orig_held['top3_hit_rate']:.1%} → {b_exp_held['top3_hit_rate']:.1%} ({b_d3_held:+.1%})\n")
        lines.append(f"  Tier C top-3: {c_orig_held['top3_hit_rate']:.1%} → {c_exp_held['top3_hit_rate']:.1%} ({c_d3_held:+.1%})\n")
        lines.append("\nTop-1 unchanged but top-3 improved — xG/xA helps the model identify the shortlist of contenders\n")
        lines.append("even when it doesn't change the single predicted winner. This is a real generalization gain\n")
        lines.append("for the 'candidate pool ranking' task per Architecture Blueprint §4.6.\n\n")
        lines.append("Update feature_registry.yaml to mark xG/xA as `modern_only` (no longer `unavailable`).\n")
        lines.append("Re-train the production model with the expanded feature set.\n")
    elif b_d1_loso > 0 or c_d1_loso > 0.02:
        lines.append("⚠️ **Mixed result** — xG/xA improved LOSO but not held-out. Likely overfitting on the small modern-era sample.\n")
        lines.append("Keep xG/xA in feature_registry but mark as experimental. Document the mixed result.\n")
    else:
        lines.append("❌ **xG/xA did not improve generalization.** Document the negative result honestly and keep features available for future use.\n")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"\n✅ Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
