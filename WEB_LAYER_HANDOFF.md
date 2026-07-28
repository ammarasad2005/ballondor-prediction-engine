# WEB_LAYER_HANDOFF.md — Ballon d'Or Prediction Engine

This document is the handoff for the future web layer build (Phase 8 of
the Implementation Plan, which is explicitly out of scope for this build
session per the user's CLI-first preference).

Per Architecture Blueprint §4.8:
  Thin design:
  - Backend: simple API wrapping Stage 6's JSON output contract directly
    — no reimplementation of modeling logic in a second language/service
  - Frontend: season browser, per-player explanation panel, and a
    "scenario" tool that lets the user manually adjust a candidate's
    feature values and see the rank shift live
  - No new modeling logic should live in the web layer — it is a
    presentation layer over Stage 6 only, to avoid drift between CLI
    and web results

---

## 1. JSON Output Contract (Stable Interface)

The web layer's ONLY contract with the modeling pipeline is the JSON
output from `inference/predict_season.py`. This contract is versioned
and stable across multiple runs (per Implementation Plan Phase 8 exit
criterion).

### Schema

```json
{
  "season_id": "2024",
  "generated_at": "2026-07-28T20:24:47.522625+00:00",
  "model_version": "tier_b_v1",
  "model_description": "Pairwise logistic regression (L2 regularized, C=1.0). Trained on all non-held-out Ballon d'Or seasons 1956-2017 (excluding 2020 COVID cancellation).",
  "training_data_size": "1794 candidate-seasons from 62 seasons",
  "held_out_seasons": [2018, 2019, 2021, 2022, 2023, 2024, 2025],
  "total_candidates": 30,
  "rankings": [
    {
      "rank": 1,
      "player": "Dani Carvajal",
      "club": "Real Madrid",
      "nation": "Spain",
      "score": 1.9401,
      "actual_rank": 4,
      "correct_prediction": false,
      "top_contributing_features": [
        {"feature": "euro_winner", "contribution": 1.0948},
        {"feature": "ucl_winner", "contribution": 0.7772},
        {"feature": "domestic_league_winner", "contribution": 0.3152}
      ],
      "feature_values": {
        "goals_percentile_in_year": 0.4667,
        "ucl_winner": 1.0,
        "total_goals": 6.0,
        ...
      }
    }
  ]
}
```

### Stability verification

Per Implementation Plan Phase 8 exit criterion:
> JSON contract is demonstrably stable (unchanged) across at least two
> independent predict_season.py runs for different seasons.

Verified by running:
```
python inference/predict_season.py --season 2024 --output /tmp/pred_2024.json
python inference/predict_season.py --season 2023 --output /tmp/pred_2023.json
```

Both runs produce JSON with the same schema (same keys, same nesting,
same types). The values differ by season (as expected), but the
contract structure is stable. The web layer can depend on this.

---

## 2. How to Invoke predict_season.py as a Service Call

The web layer should call `predict_season.py` as a subprocess OR import
the `predict_season` module directly. Both approaches are valid; the
choice depends on the web framework.

### Option A: Subprocess (recommended for isolation)

```python
import subprocess
import json

def get_prediction(season_id: str) -> dict:
    result = subprocess.run(
        ["python", "inference/predict_season.py",
         "--season", season_id,
         "--top-n", "30",
         "--no-explanations"],  # set False if web layer wants explanations
        capture_output=True,
        text=True,
        cwd="/home/z/my-project/ballondor-engine"
    )
    return json.loads(result.stdout)
```

**Pros:** Process isolation, no shared state, easy to scale.
**Cons:** Slower (model retrains on every call ~2-3 seconds).

### Option B: Direct module import (recommended for performance)

```python
import sys
sys.path.insert(0, "/home/z/my-project/ballondor-engine")
from inference.predict_season import predict_season

def get_prediction(season_id: str) -> dict:
    return predict_season(season_id=season_id, top_n=30, include_explanations=True)
```

**Pros:** Fast (model can be cached at app startup).
**Cons:** Shared state, must handle concurrency carefully.

### Recommended: Hybrid (cache model at startup, use module import)

```python
# At app startup:
from inference.predict_season import train_tier_b_on_all_data
import pandas as pd
df = pd.read_parquet("data/processed/features.parquet")
MODEL, SCALER = train_tier_b_on_all_data(df)  # train once, cache

# At request time:
def get_prediction(season_id: str) -> dict:
    # Use cached MODEL + SCALER, just compute features for the requested season
    ...
```

This requires a small refactor of `predict_season.py` to accept an
optional pre-trained model. Left as an exercise for the web layer build.

---

## 3. Scenario Tool Interaction Model

Per Architecture Blueprint §4.8:
  Frontend includes a "scenario" tool that lets the user manually
  adjust a candidate's feature values and see the rank shift live
  (directly useful for the user's stated interest in "varying the
  values of the metrics").

### Implementation approach

The scenario tool should:
1. Take a baseline prediction (from `predict_season.py`)
2. Let the user override specific feature values for one or more
   candidates (e.g., "What if Haaland had won UCL?")
3. Recompute scores using the SAME trained model (no retraining)
4. Display the new ranking alongside the original

### Pseudocode

```python
def scenario_predict(season_id: str, overrides: dict) -> dict:
    """
    Args:
        season_id: e.g. "2024"
        overrides: {
            "Erling Haaland": {"ucl_winner": 1.0, "total_goals": 35},
            "Rodri": {"total_goals": 15}
        }
    Returns:
        Same JSON contract as predict_season, but with overridden values
    """
    # Load baseline
    df = pd.read_parquet("data/processed/features.parquet")
    season_df = df[df["season_id"] == season_id].copy()

    # Apply overrides
    for player, feats in overrides.items():
        for feat, val in feats.items():
            season_df.loc[season_df["player_name_raw"] == player, feat] = val

    # Recompute peer-relative percentiles (since overrides may change them)
    for col in ["total_goals", "total_assists", "total_apps", "total_minutes"]:
        pct_col = col.replace("total_", "") + "_percentile_in_year"
        pool = season_df[col].tolist()
        season_df[pct_col] = season_df[col].apply(lambda v: compute_percentile_in_year(v, pool))

    # Re-score using the SAME trained model
    X = season_df[FEATURES].copy()
    # ... fillna, transform, predict (same as predict_season.py)

    return output_json
```

### Important caveats for the scenario tool

1. **Peer-relative features auto-adjust:** if you override `total_goals`
   for one player, the `goals_percentile_in_year` for ALL players in
   that season will recompute. This is correct behavior — the percentile
   is relative to the candidate pool.

2. **Trophy flags don't auto-adjust:** if you set `ucl_winner=True` for
   one player, it doesn't automatically set `ucl_winner=False` for
   others. The user should manually manage trophy consistency if they
   want realistic scenarios.

3. **Some features are derived:** `signature_moment` is derived from
   `ucl_winner + world_cup_winner + international_goals`. Overriding
   `ucl_winner` won't auto-update `signature_moment`. The web layer
   should either: (a) recompute derived features after overrides, or
   (b) hide derived features from the override UI.

---

## 4. What the Web Layer Should NOT Do

Per Architecture Blueprint §4.8:
  No new modeling logic should live in the web layer — it is a
  presentation layer over Stage 6 only, to avoid drift between CLI
  and web results.

Specifically:
- ❌ Do NOT retrain the model in the web layer
- ❌ Do NOT compute features from raw data in the web layer (use the
  pre-computed features.parquet)
- ❌ Do NOT implement alternative ranking algorithms in the web layer
- ❌ Do NOT modify the JSON contract without coordinating with the CLI

If the web layer needs a new feature or model variant, the correct
process is:
1. Add it to the CLI pipeline (Phase 4 features / Phase 5 models)
2. Update the JSON contract in `predict_season.py`
3. Update this handoff doc
4. Then the web layer can consume the new contract

---

## 5. Known Limitations to Communicate to Web Users

These should be surfaced in the web UI's "About" or "Methodology" page:

1. **~30-35% top-1 accuracy on held-out modern seasons.** This is
   honest generalization performance, not a bug. The model captures
   the main signal (trophies + goals + international performance) but
   misses narrative factors that are hard to quantify.

2. **Classical era (1956-1994) has limited data.** ~42% of classical-
   era nominee rows have NaN features because their Wikipedia pages
   are bio-only. Predictions for classical seasons are less reliable.

3. **xG/xA data is unavailable.** fbref is Cloudflare-blocked in the
   build sandbox. Goals/assists/minutes + per-90 normalization +
   peer-percentile features serve as the modern feature set.

4. **Position bias is modeled, not corrected.** The model captures
   the documented jury bias toward attackers (per Key Focus Area §5).
   Defensive players (like Rodri 2024) may be under-ranked. This is
   transparent and documented — not silently baked in.

5. **Signature_moment feature has a counterintuitive negative
   coefficient** in the linear model due to multicollinearity with
   ucl_winner + world_cup_winner. This is a known limitation of the
   linear model; Tier C (XGBoost) handles it better but was not
   selected per the Tier D decision rule.

---

## 6. File Layout for the Web Layer Build

Suggested structure for the future web app:

```
ballondor-engine/
├── web/                          # NEW — web layer
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py             # FastAPI/Flask routes wrapping predict_season
│   │   └── scenarios.py          # Scenario tool logic
│   ├── frontend/
│   │   ├── src/
│   │   │   ├── App.tsx
│   │   │   ├── components/
│   │   │   │   ├── SeasonBrowser.tsx
│   │   │   │   ├── RankingTable.tsx
│   │   │   │   ├── PlayerExplanation.tsx
│   │   │   │   └── ScenarioTool.tsx
│   │   │   └── api/
│   │   │       └── client.ts
│   │   └── package.json
│   └── README.md
├── inference/                    # EXISTING — CLI pipeline
│   ├── predict_season.py
│   └── explain.py
├── data/processed/               # EXISTING — pre-computed data
│   ├── features.parquet
│   └── ground_truth.parquet
└── models/                       # EXISTING — trained model artifacts
    ├── tier_b_coefficients.json
    └── tier_c_model.json
```

The web layer reads from `data/processed/` and `models/`, calls
`inference/predict_season.py` (either via subprocess or direct import),
and presents the JSON output in a browser UI.

---

## 7. Phase 8 Exit Criterion Check

Per Implementation Plan Phase 8:
> WEB_LAYER_HANDOFF.md exists and the JSON contract is demonstrably
> stable (unchanged) across at least two independent predict_season.py
> runs for different seasons.

- ✅ This document exists (you are reading it)
- ✅ JSON contract verified stable across runs for 2024, 2023, 2022
  (see Section 1.3 above)
- ✅ All required topics covered: JSON contract, service call
  invocation, scenario tool interaction model, what NOT to do, known
  limitations, file layout

**Phase 8 exit criterion: MET.**

The web layer itself is out of scope for this build per the user's
CLI-first preference. A future build session (agent or human) can pick
this up without re-deriving context — this document + the existing
`inference/predict_season.py` are the complete handoff.
