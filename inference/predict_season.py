"""Inference pipeline — predict a season's Ballon d'Or ranking.

Per Architecture Blueprint §4.7 + Implementation Plan Phase 7:
  1. Given a candidate pool for a not-yet-decided season, compute features
     (reusing Phase 4 logic exactly — no parallel feature logic), run the
     selected model, output the JSON contract.
  2. Per-candidate explanation: top contributing features (direct coefficient
     × feature-value contribution for the linear model).
  3. CLI wrapper: `python -m inference.predict_season --season 2026`
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"

FEATURES = [
    "goals_percentile_in_year", "assists_percentile_in_year",
    "apps_percentile_in_year", "minutes_percentile_in_year",
    "ucl_winner", "ucl_runner_up",
    "domestic_league_winner", "domestic_league_runner_up",
    "world_cup_winner", "world_cup_runner_up",
    "euro_winner", "copa_america_winner", "international_tournament_year",
    "signature_moment", "club_prestige_tier", "previous_ballon_dor_winner",
    "total_goals", "total_assists", "total_apps",
    "international_goals", "international_apps",
]

HELD_OUT_SEASONS = {2018, 2019, 2021, 2022, 2023, 2024, 2025}


def build_pairs_for_training(df: pd.DataFrame):
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


def train_tier_b_on_all_data(df: pd.DataFrame):
    train_df = df[~df["award_year"].isin(HELD_OUT_SEASONS)]
    X_train, y_train = build_pairs_for_training(train_df)
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=1000, random_state=20260728)
    model.fit(X_train_scaled, y_train)
    return model, scaler


def compute_feature_contributions(model, scaler, feature_values: np.ndarray) -> list[dict]:
    scaled = scaler.transform(feature_values.reshape(1, -1))[0]
    coefs = model.coef_[0]
    contributions = []
    for i, feature in enumerate(FEATURES):
        contribution = float(coefs[i] * scaled[i])
        contributions.append({
            "feature": feature,
            "value": float(feature_values[i]) if not pd.isna(feature_values[i]) else None,
            "coefficient": float(coefs[i]),
            "contribution": contribution,
        })
    contributions.sort(key=lambda x: -abs(x["contribution"]))
    return contributions


def predict_season(season_id: str, top_n: int = 10, include_explanations: bool = True) -> dict:
    df = pd.read_parquet(FEATURES_PARQUET)
    season_df = df[df["season_id"] == season_id].copy()
    if len(season_df) == 0:
        return {
            "error": f"No candidates found for season {season_id}",
            "season_id": season_id,
        }

    print(f"Training Tier B on all non-held-out data...")
    model, scaler = train_tier_b_on_all_data(df)

    X = season_df[FEATURES].copy()
    for col in X.columns:
        if X[col].dtype == bool:
            X[col] = X[col].astype(int)
        X[col] = X[col].fillna(0)
    X_values = X.values

    X_scaled = scaler.transform(X_values)
    scores = model.decision_function(X_scaled)

    season_df["predicted_score"] = scores
    season_df = season_df.sort_values("predicted_score", ascending=False).reset_index(drop=True)
    season_df["predicted_rank"] = range(1, len(season_df) + 1)

    # CRITICAL: X_values is in the ORIGINAL season_df order, but season_df
    # has been re-sorted. We need to re-sort X_values to match the new order.
    # Use the predicted_score to align (since predicted_score is in both
    # season_df and was computed from X_values in original order).
    # Easier approach: rebuild X_values from the sorted season_df.
    X_sorted = season_df[FEATURES].copy()
    for col in X_sorted.columns:
        if X_sorted[col].dtype == bool:
            X_sorted[col] = X_sorted[col].astype(int)
        X_sorted[col] = X_sorted[col].fillna(0)
    X_values_sorted = X_sorted.values

    rankings = []
    for idx, row in season_df.head(top_n).iterrows():
        ranking = {
            "rank": int(row["predicted_rank"]),
            "player": row["player_name_raw"],
            "club": row.get("club_at_time", ""),
            "nation": row.get("nation_team", ""),
            "score": float(row["predicted_score"]),
        }
        if include_explanations:
            feature_values = X_values_sorted[idx]
            contributions = compute_feature_contributions(model, scaler, feature_values)
            ranking["top_contributing_features"] = [
                {"feature": c["feature"], "contribution": round(c["contribution"], 4)}
                for c in contributions[:5]
            ]
            ranking["feature_values"] = {
                feat: (float(X_values_sorted[idx][i]) if not pd.isna(X_values_sorted[idx][i]) else None)
                for i, feat in enumerate(FEATURES)
            }
        if "rank" in row and not pd.isna(row.get("rank")):
            ranking["actual_rank"] = int(row["rank"])
            ranking["correct_prediction"] = bool(ranking["rank"] == ranking["actual_rank"])
        rankings.append(ranking)

    output = {
        "season_id": season_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": "tier_b_v1",
        "model_description": "Pairwise logistic regression (L2 regularized, C=1.0). Trained on all non-held-out Ballon d'Or seasons 1956-2017 (excluding 2020 COVID cancellation).",
        "training_data_size": f"{len(df[~df['award_year'].isin(HELD_OUT_SEASONS)])} candidate-seasons from {df[~df['award_year'].isin(HELD_OUT_SEASONS)]['award_year'].nunique()} seasons",
        "held_out_seasons": sorted(list(HELD_OUT_SEASONS)),
        "total_candidates": len(season_df),
        "rankings": rankings,
    }
    return output


def main():
    parser = argparse.ArgumentParser(description="Predict Ballon d'Or ranking for a season")
    parser.add_argument("--season", required=True, help="Season ID (e.g. 2024, 2026)")
    parser.add_argument("--top-n", type=int, default=10, help="Number of top candidates to include")
    parser.add_argument("--no-explanations", action="store_true", help="Skip per-candidate feature contributions")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file path (default: stdout)")
    args = parser.parse_args()

    print(f"Predicting Ballon d'Or for season {args.season}...")
    result = predict_season(
        season_id=args.season,
        top_n=args.top_n,
        include_explanations=not args.no_explanations,
    )

    output_json = json.dumps(result, indent=2, ensure_ascii=False, default=str)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"\n✅ Wrote prediction to {args.output}")
    else:
        print()
        print(output_json)

    if "rankings" in result:
        print()
        print("=" * 70)
        print(f"Top-5 predicted for {args.season}:")
        print("=" * 70)
        for r in result["rankings"][:5]:
            actual = f" (actual: #{r.get('actual_rank', '?')})" if "actual_rank" in r else ""
            print(f"  #{r['rank']}  {r['player']:30}  score={r['score']:+.3f}{actual}")
            if r.get("top_contributing_features"):
                for c in r["top_contributing_features"][:3]:
                    print(f"        {c['feature']:35}  {c['contribution']:+.4f}")


if __name__ == "__main__":
    main()
