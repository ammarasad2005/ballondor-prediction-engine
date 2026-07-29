"""P0-C: Geographic representation features.

Per implementation plan: 33% of missed winners won partly due to geographic
representation factors (Weah=only African, Blokhin/Belanov=Soviet, etc.).

All features computed from existing ground truth — NO new scraping needed.

Features added:
  - nation_prior_winners_count: # of prior Ballon d'Or winners from this nation
  - continent_prior_winners_count: same at continent level
  - nation_years_since_last_winner: years since nation last won (999 if never)
  - league_visibility_tier: 1-4 tier of player's domestic league
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
FEATURES_PARQUET = PROJECT_ROOT / "data" / "processed" / "features.parquet"
GT_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"

# -----------------------------------------------------------------------------
# Nation → Continent mapping
# -----------------------------------------------------------------------------

NATION_TO_CONTINENT = {
    # Europe
    "England": "Europe", "Scotland": "Europe", "Wales": "Europe", "Northern Ireland": "Europe",
    "France": "Europe", "Germany": "Europe", "West Germany": "Europe", "East Germany": "Europe",
    "Italy": "Europe", "Spain": "Europe", "Portugal": "Europe", "Netherlands": "Europe",
    "Belgium": "Europe", "Sweden": "Europe", "Denmark": "Europe", "Norway": "Europe",
    "Finland": "Europe", "Ireland": "Europe", "Republic of Ireland": "Europe",
    "Austria": "Europe", "Switzerland": "Europe", "Czech Republic": "Europe",
    "Czechoslovakia": "Europe", "Hungary": "Europe", "Poland": "Europe",
    "Romania": "Europe", "Bulgaria": "Europe", "Yugoslavia": "Europe",
    "Croatia": "Europe", "Serbia": "Europe", "Serbia and Montenegro": "Europe",
    "Bosnia and Herzegovina": "Europe", "Slovenia": "Europe",
    "Soviet Union": "Europe", "Russia": "Europe", "Ukraine": "Europe",
    "Belarus": "Europe", "Georgia": "Europe", "Lithuania": "Europe",
    "Greece": "Europe", "Turkey": "Europe",
    # South America
    "Brazil": "South America", "Argentina": "South America", "Uruguay": "South America",
    "Colombia": "South America", "Chile": "South America", "Peru": "South America",
    "Paraguay": "South America", "Ecuador": "South America", "Venezuela": "South America",
    "Bolivia": "South America",
    # Africa
    "Liberia": "Africa", "Egypt": "Africa", "Nigeria": "Africa", "Ghana": "Africa",
    "Cameroon": "Africa", "Senegal": "Africa", "Ivory Coast": "Africa", "Côte d'Ivoire": "Africa",
    "Morocco": "Africa", "Algeria": "Africa", "Tunisia": "Africa", "South Africa": "Africa",
    "Mali": "Africa", "Guinea": "Africa", "Gabon": "Africa", "Congo": "Africa",
    "DR Congo": "Africa", "Zaire": "Africa", "Kenya": "Africa",
    # Asia
    "Japan": "Asia", "South Korea": "Asia", "Korea Republic": "Asia", "Korea DPR": "Asia",
    "China": "Asia", "Iran": "Asia", "Iraq": "Asia", "Saudi Arabia": "Asia",
    "Australia": "Asia",  # AFC member
    # North America
    "United States": "North America", "USA": "North America", "Mexico": "North America",
    "Costa Rica": "North America", "Canada": "North America", "Honduras": "North America",
}

# -----------------------------------------------------------------------------
# League visibility tier (based on club_at_time)
# -----------------------------------------------------------------------------

TIER_1_LEAGUES = {
    # England
    "manchester united", "manchester city", "liverpool", "chelsea", "arsenal",
    "tottenham", "everton", "leicester",
    # Spain
    "real madrid", "barcelona", "atletico madrid", "atlético madrid", "atletico de madrid",
    "valencia", "sevilla", "villarreal",
    # Italy
    "juventus", "ac milan", "milan", "inter milan", "internazionale", "inter",
    "napoli", "roma", "as roma", "lazio", "fiorentina",
    # Germany
    "bayern munich", "bayern de múnich", "fc bayern münchen", "borussia dortmund",
    "bayer leverkusen", "schalke", "rb leipzig",
    # France
    "paris saint-germain", "psg", "paris saint germain", "marseille", "monaco",
    "lyon", "ol", "lille",
}

TIER_2_LEAGUES = {
    # Portugal
    "benfica", "sporting cp", "sporting lisbon", "porto", "fc porto",
    # Netherlands
    "ajax", "psv", "psv eindhoven", "feyenoord",
    # Russia
    "zenit", "cska moscow", "spartak moscow",
    # Turkey
    "galatasaray", "fenerbahce", "fenerbahçe", "besiktas", "beşiktaş",
    # Scotland
    "celtic", "rangers",
    # Belgium
    "anderlecht", "club brugge",
    # Ukraine
    "dynamo kyiv", "dynamo kiev", "shakhtar donetsk",
    # Other European
    "red bull salzburg", "basel",
}

def league_visibility_tier(club: str) -> int:
    """Return league visibility tier 1-4 based on club name."""
    if not club:
        return 4
    club_lower = club.lower().strip()
    for elite in TIER_1_LEAGUES:
        if elite in club_lower:
            return 1
    for strong in TIER_2_LEAGUES:
        if strong in club_lower:
            return 2
    # Default: tier 3 if it's a European club (heuristic), tier 4 otherwise
    return 3


def get_continent(nation: str) -> str:
    """Map nation to continent. Returns 'Other' if unknown."""
    if not nation:
        return "Unknown"
    return NATION_TO_CONTINENT.get(nation.strip(), "Other")


def main():
    print("=" * 70)
    print("P0-C: Geographic Representation Features")
    print("=" * 70)

    # Load data
    df = pd.read_parquet(FEATURES_PARQUET)
    gt = pd.read_parquet(GT_PARQUET)

    # Get nation_team from ground truth (more complete than features)
    nation_lookup = gt.set_index(["season_id", "player_name_raw"])["nation_team"].to_dict()
    df["nation_team"] = df.apply(
        lambda r: nation_lookup.get((r["season_id"], r["player_name_raw"]), r.get("nation_team", "")),
        axis=1
    )

    # Build winner history: sorted list of (year, nation, continent) for all prior winners
    winners = gt[gt["rank"] == 1][["award_year", "player_name_raw", "nation_team"]].sort_values("award_year")
    winners["continent"] = winners["nation_team"].apply(get_continent)

    print(f"Total winners in history: {len(winners)}")
    print(f"Nation distribution of winners:")
    print(winners["nation_team"].value_counts().head(15))

    # Compute features
    print("\nComputing geographic features...")
    df["continent"] = df["nation_team"].apply(get_continent)
    df["league_visibility_tier"] = df["club_at_time"].apply(league_visibility_tier)

    # For each row, count prior winners from same nation/continent
    nation_prior = []
    continent_prior = []
    years_since_nation = []

    for _, row in df.iterrows():
        award_year = int(row["award_year"])
        nation = row["nation_team"]
        continent = row["continent"]

        # Prior winners from same nation (strictly before this year)
        prior_nation = winners[(winners["award_year"] < award_year) & (winners["nation_team"] == nation)]
        nation_prior.append(len(prior_nation))

        # Prior winners from same continent
        prior_continent = winners[(winners["award_year"] < award_year) & (winners["continent"] == continent)]
        continent_prior.append(len(prior_continent))

        # Years since last winner from this nation
        if len(prior_nation) > 0:
            last_year = prior_nation["award_year"].max()
            years_since_nation.append(award_year - last_year)
        else:
            years_since_nation.append(999)  # never won

    df["nation_prior_winners_count"] = nation_prior
    df["continent_prior_winners_count"] = continent_prior
    df["nation_years_since_last_winner"] = years_since_nation

    # Summary
    print("\nFeature summary:")
    print(f"  nation_prior_winners_count: mean={df['nation_prior_winners_count'].mean():.1f}, "
          f"max={df['nation_prior_winners_count'].max()}")
    print(f"  continent_prior_winners_count: mean={df['continent_prior_winners_count'].mean():.1f}, "
          f"max={df['continent_prior_winners_count'].max()}")
    print(f"  nation_years_since_last_winner: mean={df[df['nation_years_since_last_winner']<999]['nation_years_since_last_winner'].mean():.1f} "
          f"(excluding 999=never)")
    print(f"  league_visibility_tier distribution:")
    print(df["league_visibility_tier"].value_counts().sort_index())

    # Sanity check: known "first winner from nation" cases
    print("\nSanity check — 'first winner from nation' cases:")
    first_winners = [
        (1956, "Stanley Matthews", "England"),
        (1995, "George Weah", "Liberia"),
        (1975, "Oleg Blokhin", "Soviet Union"),
        (1967, "Flórián Albert", "Hungary"),
    ]
    for year, player, nation in first_winners:
        sub = df[(df["award_year"] == year) & (df["player_name_raw"] == player)]
        if len(sub):
            r = sub.iloc[0]
            print(f"  {year} {player} ({nation}): "
                  f"nation_prior={r['nation_prior_winners_count']}, "
                  f"continent_prior={r['continent_prior_winners_count']}, "
                  f"years_since={r['nation_years_since_last_winner']}")

    # Save
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, FEATURES_PARQUET, compression="snappy")
    print(f"\n✅ Updated {FEATURES_PARQUET} ({df.shape[1]} cols)")

    # Add new features to feature lists in all model/inference/validation files
    new_features = [
        "nation_prior_winners_count",
        "continent_prior_winners_count",
        "nation_years_since_last_winner",
        "league_visibility_tier",
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
        if "nation_prior_winners_count" not in content:
            # Add after "data_completeness_score" in FEATURES lists
            content = content.replace(
                '"data_completeness_score",',
                '"data_completeness_score",\n    "nation_prior_winners_count", "continent_prior_winners_count",\n    "nation_years_since_last_winner", "league_visibility_tier",',
            )
            path.write_text(content)
            try:
                compile(content, str(path), "exec")
                print(f"  ✅ {filepath}")
            except SyntaxError as e:
                print(f"  ❌ {filepath} at line {e.lineno}: {e.msg}")
        else:
            print(f"  ⏭️  {filepath} (already has features)")

    # Also add to TIER_A_WEIGHTS in run_validation.py
    path = PROJECT_ROOT / "validation/run_validation.py"
    content = path.read_text()
    if '"nation_prior_winners_count":' not in content:
        content = content.replace(
            '"data_completeness_score": 0.3,\n',
            '"data_completeness_score": 0.3,\n    "nation_prior_winners_count": -0.5,  # fewer prior winners = more notable\n    "continent_prior_winners_count": -0.3,\n    "nation_years_since_last_winner": 0.5,  # long drought = more notable\n    "league_visibility_tier": -0.2,  # lower tier = penalty\n',
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
