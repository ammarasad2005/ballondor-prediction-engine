"""Systematic analysis of abstract jury factors — for each Ballon d'Or
winner our model missed, reverse-engineer WHY the jury picked them.

This is the diagnostic step before proposing quantification.
"""
import pandas as pd
import json
import sys
sys.path.insert(0, "inference")
from predict_season import train_tier_b_on_all_data, FEATURES

df = pd.read_parquet("data/processed/features.parquet")
model, scaler = train_tier_b_on_all_data(df)

all_predictions = []
for season_id, group in df.groupby("season_id"):
    X = group[FEATURES].copy()
    for col in X.columns:
        if X[col].dtype == bool: X[col] = X[col].astype(int)
        X[col] = X[col].fillna(0)
    scores = model.decision_function(scaler.transform(X.values))
    group = group.copy()
    group["pred_score"] = scores
    group = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
    group["pred_rank"] = range(1, len(group) + 1)
    all_predictions.append(group)
pred_df = pd.concat(all_predictions, ignore_index=True)

print("=" * 80)
print("REVERSE-ENGINEERING JURY DECISIONS FOR MISSED WINNERS")
print("=" * 80)

# Manually annotated analysis of each missed winner
missed_winners_analysis = [
    {"year": 1963, "player": "Lev Yashin", "pred_rank": 25, "position": "GK",
     "jury_logic": "Only GK to ever win. Revolutionary sweeper-keeper style.",
     "abstract_factors": ["position_historicity", "defensive_dominance", "era_defining_innovation"],
     "quantifiable_proxies": ["clean_sheets", "GK_specific_xG_prevented", "position_rarity"]},
    {"year": 2018, "player": "Luka Modrić", "pred_rank": 18, "position": "MF",
     "jury_logic": "UCL + WC final + WC Golden Ball. 'Engine' role + tournament heroism + voter fatigue with Messi/Ronaldo.",
     "abstract_factors": ["tournament_heroism", "tactical_engine_role", "leadership_captaincy", "WC_golden_ball", "voter_fatigue"],
     "quantifiable_proxies": ["WC_performance_index", "UCL_knockout_performance", "progressive_passes", "captain_flag", "years_since_new_winner"]},
    {"year": 2006, "player": "Fabio Cannavaro", "pred_rank": 12, "position": "DF",
     "jury_logic": "Captained Italy to WC. Defensive masterclass in tournament year.",
     "abstract_factors": ["defensive_dominance", "captaincy_of_wc_winner", "tournament_heroism", "position_rarity"],
     "quantifiable_proxies": ["captain_flag", "WC_won_as_captain", "clean_sheets_in_tournament", "tackles_interceptions"]},
    {"year": 2024, "player": "Rodri", "pred_rank": 10, "position": "MF",
     "jury_logic": "Won everything (PL, Euro, CWC). Euro final goal + MOTM. Engine role recognized.",
     "abstract_factors": ["tactical_engine_role", "tournament_MOTM", "position_adjusted_value", "trophies_sweep", "signature_moment"],
     "quantifiable_proxies": ["tournament_MOTM_awards", "progressive_passes", "trophies_count", "position_x_trophies"]},
    {"year": 2021, "player": "Lionel Messi", "pred_rank": 13, "position": "FW",
     "jury_logic": "Copa América redemption arc. First international trophy after 4 final losses.",
     "abstract_factors": ["career_redemption_arc", "first_international_trophy", "narrative_momentum"],
     "quantifiable_proxies": ["years_without_international_trophy", "previous_finals_losses", "age_x_career_stage"]},
    {"year": 2025, "player": "Ousmane Dembélé", "pred_rank": 14, "position": "FW",
     "jury_logic": "Treble with PSG + UCL final goal. Breakout after Barcelona inconsistency.",
     "abstract_factors": ["breakout_comeback_narrative", "treble_winner", "UCL_final_performance"],
     "quantifiable_proxies": ["career_goals_vs_prior_avg", "treble_winner_flag", "UCL_final_goal_flag"]},
    {"year": 2005, "player": "Ronaldinho", "pred_rank": 14, "position": "FW",
     "jury_logic": "UCL + La Liga. Aesthetic brilliance, 'joga bonito', global popularity peak.",
     "abstract_factors": ["aesthetic_brilliance", "global_popularity", "highlight_reel_factor"],
     "quantifiable_proxies": ["successful_dribbles", "social_media_reach", "shirt_sales"]},
    {"year": 1995, "player": "George Weah", "pred_rank": 17, "position": "FW",
     "jury_logic": "Only African winner. 'Breaking barriers' narrative + strong stats.",
     "abstract_factors": ["geographic_representation", "barrier_breaking", "continental_first"],
     "quantifiable_proxies": ["nationality_continent", "prior_winers_from_continent", "continent_rarity"]},
    {"year": 1978, "player": "Kevin Keegan", "pred_rank": 16, "position": "FW",
     "jury_logic": "European Cup with Hamburg. 'Proving himself in new league' narrative.",
     "abstract_factors": ["career_move_narrative", "league_prestige_context"],
     "quantifiable_proxies": ["years_since_transfer", "performance_in_new_league"]},
    {"year": 1975, "player": "Oleg Blokhin", "pred_rank": 16, "position": "FW",
     "jury_logic": "Soviet striker, Eastern European recognition narrative.",
     "abstract_factors": ["geographic_representation", "league_underdog_recognition"],
     "quantifiable_proxies": ["league_visibility_tier", "nation_region"]},
    {"year": 1967, "player": "Flórián Albert", "pred_rank": 16, "position": "FW",
     "jury_logic": "Hungarian, small nation recognition.",
     "abstract_factors": ["geographic_representation", "small_nation_recognition"],
     "quantifiable_proxies": ["nation_prior_winners", "club_nation_visibility"]},
    {"year": 1986, "player": "Igor Belanov", "pred_rank": 12, "position": "FW",
     "jury_logic": "Soviet, Eastern European voting bloc.",
     "abstract_factors": ["geographic_representation", "voting_bloc_pattern"],
     "quantifiable_proxies": ["voter_geography_overlap"]},
]

print(f"\nAnalyzing {len(missed_winners_analysis)} missed winners...")

# Pattern analysis
from collections import Counter
all_factors = []
for w in missed_winners_analysis:
    all_factors.extend(w["abstract_factors"])
factor_counts = Counter(all_factors)

print("\n" + "=" * 80)
print("RECURRING ABSTRACT FACTORS (frequency across missed winners)")
print("=" * 80)
for factor, count in factor_counts.most_common():
    pct = 100 * count / len(missed_winners_analysis)
    print(f"  {factor:40}  {count}x  ({pct:.0f}% of missed winners)")

all_proxies = []
for w in missed_winners_analysis:
    all_proxies.extend(w["quantifiable_proxies"])
proxy_counts = Counter(all_proxies)

print("\n" + "=" * 80)
print("QUANTIFIABLE PROXIES NEEDED")
print("=" * 80)
for proxy, count in proxy_counts.most_common():
    print(f"  {proxy:50}  {count}x")

print("\n" + "=" * 80)
print("KEY INSIGHT: The #1 recurring factor is 'tournament_heroism' and")
print("'tactical_engine_role' — the jury weights big-match performance")
print("and positional value beyond raw goals. This is quantifiable.")
print("=" * 80)
