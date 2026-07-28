"""Explanation layer — per-candidate 'why does this player rank where they do'.

Per Architecture Blueprint §4.7 + Implementation Plan Phase 7 task 2:
  Per-candidate contribution breakdown: direct coefficient × feature-value
  contribution for the linear model (Tier B).

Per Key Focus Areas §10 — explanation quality is a deliverable, not a
nice-to-have:
  When the agent reaches Implementation Plan Phase 7's manual spot check,
  the explanation output should be judged not just on 'is the ranking
  plausible' but 'does the stated reasoning for each ranking match what a
  knowledgeable football follower would actually cite as that player's
  case for/against.'

This module takes the JSON output from predict_season.py and produces a
human-readable explanation for each ranked candidate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

# -----------------------------------------------------------------------------
# Feature name -> human-readable description mapping
# -----------------------------------------------------------------------------

FEATURE_DESCRIPTIONS = {
    "goals_percentile_in_year": "Goals scored vs other nominees this year (percentile)",
    "assists_percentile_in_year": "Assists vs other nominees this year (percentile)",
    "apps_percentile_in_year": "Appearances vs other nominees this year (percentile)",
    "minutes_percentile_in_year": "Minutes played vs other nominees this year (percentile)",
    "ucl_winner": "Won UEFA Champions League",
    "ucl_runner_up": "Lost UCL final",
    "domestic_league_winner": "Won domestic league title",
    "domestic_league_runner_up": "Finished 2nd in domestic league",
    "world_cup_winner": "Won FIFA World Cup",
    "world_cup_runner_up": "Lost World Cup final",
    "euro_winner": "Won UEFA European Championship",
    "copa_america_winner": "Won Copa América",
    "international_tournament_year": "Major international tournament held this year",
    "signature_moment": "Iconic individual performance (e.g., UCL+WC double)",
    "club_prestige_tier": "Club prestige (1=elite, 4=small)",
    "previous_ballon_dor_winner": "Won Ballon d'Or in a prior year",
    "total_goals": "Total goals across all club competitions",
    "total_assists": "Total assists across all club competitions",
    "total_apps": "Total appearances across all club competitions",
    "international_goals": "Goals scored for national team",
    "international_apps": "Appearances for national team",
    # xG/xA features (from Understat, modern era only)
    "xg": "Expected goals (quality of chances, from Understat)",
    "xa": "Expected assists (chance-creation value, from Understat)",
    "npxg": "Non-penalty expected goals (excludes penalty xG)",
    "xg_per90": "Expected goals per 90 minutes played",
    "xa_per90": "Expected assists per 90 minutes played",
    "xg_overperformance": "Goals minus xG (positive = clinical finishing)",
}


def explain_prediction(prediction_json: dict, top_n: int = 5) -> str:
    """Generate a human-readable explanation of a season prediction.

    Args:
        prediction_json: output from predict_season.predict_season()
        top_n: number of top candidates to explain

    Returns:
        Markdown-formatted explanation text
    """
    lines = []
    lines.append(f"# Ballon d'Or {prediction_json['season_id']} — Prediction Explanation\n\n")
    lines.append(f"Generated: {prediction_json['generated_at']}\n")
    lines.append(f"Model: {prediction_json['model_version']}\n")
    lines.append(f"Training data: {prediction_json.get('training_data_size', 'N/A')}\n\n")

    rankings = prediction_json.get("rankings", [])[:top_n]
    if not rankings:
        lines.append("No predictions available.\n")
        return "".join(lines)

    lines.append(f"## Top {len(rankings)} Predicted Candidates\n\n")
    for r in rankings:
        actual_note = ""
        if "actual_rank" in r:
            actual_note = f" (actual: #{r['actual_rank']})"
            if r.get("correct_prediction"):
                actual_note += " ✅"
            else:
                actual_note += " ❌"

        lines.append(f"### #{r['rank']} — {r['player']}{actual_note}\n\n")
        lines.append(f"- **Club:** {r.get('club', 'N/A')}\n")
        lines.append(f"- **Nationality:** {r.get('nation', 'N/A')}\n")
        lines.append(f"- **Predicted score:** {r['score']:+.4f}\n\n")

        if r.get("top_contributing_features"):
            lines.append("**Top contributing features:**\n\n")
            for c in r["top_contributing_features"][:5]:
                feature = c["feature"]
                contribution = c["contribution"]
                desc = FEATURE_DESCRIPTIONS.get(feature, feature)
                direction = "↑ boosted" if contribution > 0 else "↓ penalized"
                lines.append(f"- **{feature}** ({direction} by {abs(contribution):.4f}): {desc}\n")
            lines.append("\n")

        # Add a plain-language summary
        if r.get("top_contributing_features"):
            positive = [c for c in r["top_contributing_features"] if c["contribution"] > 0]
            negative = [c for c in r["top_contributing_features"] if c["contribution"] < 0]
            summary_parts = []
            if positive:
                top_pos = positive[0]
                summary_parts.append(f"boosted mainly by {FEATURE_DESCRIPTIONS.get(top_pos['feature'], top_pos['feature']).lower()}")
            if negative:
                top_neg = negative[0]
                summary_parts.append(f"penalized mainly by {FEATURE_DESCRIPTIONS.get(top_neg['feature'], top_neg['feature']).lower()}")
            if summary_parts:
                lines.append(f"**Summary:** This player is {', '.join(summary_parts)}.\n\n")

    lines.append("---\n\n")
    lines.append("## How to Read This\n\n")
    lines.append("- Each candidate's predicted score = Σ (coefficient × feature value) across all 21 features.\n")
    lines.append("- Positive contributions push the player UP in the ranking; negative push DOWN.\n")
    lines.append("- The model is a pairwise logistic ranker — it learned which feature differences distinguish higher-ranked from lower-ranked players in historical Ballon d'Or voting.\n")
    lines.append("- A player ranking high with modest stats likely has strong team-trophy features (UCL winner, World Cup winner) or narrative features (previous Ballon d'Or winner, club prestige).\n")
    lines.append("- A player ranking low despite high goals likely lacks team trophies or plays for a lower-prestige club (the model captures documented jury biases transparently per Key Focus Area §5).\n")

    return "".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Explain a Ballon d'Or prediction")
    parser.add_argument("--input", required=True, help="Path to prediction JSON from predict_season.py")
    parser.add_argument("--top-n", type=int, default=5, help="Number of top candidates to explain")
    parser.add_argument("--output", type=str, default=None, help="Output markdown file path")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        prediction = json.load(f)

    explanation = explain_prediction(prediction, top_n=args.top_n)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(explanation, encoding="utf-8")
        print(f"✅ Wrote explanation to {args.output}")
    else:
        print(explanation)


if __name__ == "__main__":
    main()
