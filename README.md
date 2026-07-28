# Ballon d'Or Prediction Engine

A learning-to-rank system that models the Ballon d'Or jury's revealed
preferences from historical voting outcomes (1956–present), then generalizes
to rank the current season's candidate pool with per-candidate explanations.

## Architecture

Seven-stage pipeline (Architecture Blueprint §3):

```
Stage 0  Season Scope         ground_truth.parquet — the join backbone
Stage 1  Data Acquisition     raw/*.parquet, *.jsonl (checkpointed, idempotent)
Stage 2  Entity Resolution    candidate_seasons.parquet (long table)
Stage 3  Feature Engineering  features.parquet (versioned schema, era-tagged)
Stage 4  Modeling             Tiers A (heuristic) → B (pairwise linear) → C (GBM) → D (selection)
Stage 5  Validation           LOSO + expanding-window CV + one-shot held-out eval
Stage 6  Inference            predict_season.py + explain.py + CLI
Stage 7  (later)              Web layer — out of scope for this build
```

## Design principles (non-negotiable, see Architecture Blueprint §2)

- **P1** Rank, don't regress — target is relative order within a season's pool
- **P2** Generalize, don't memorize — held-out seasons looked at exactly once
- **P3** Interpretability first, complexity second — Tier B is primary, Tier C only if it earns its complexity
- **P4** Two eras, one philosophy — modern full-depth, classical simplified, same logic
- **P5** Explicit bias handling — bias is a feature and a documented limitation, never silent
- **P6** Agent-native — autonomous scraping, iterative cleaning, checkpointed state

## Repo layout

See `01_ARCHITECTURE_BLUEPRINT.md` §5. Key directories:

- `data/{raw,interim,processed}/` — checkpointed, never overwrite raw
- `scrapers/` — one module per source, all idempotent
- `entity_resolution/` — canonical IDs, alias table, fuzzy matching, QA report
- `features/` — feature_registry.yaml + per-family modules + build_features.py
- `models/` — Tiers A/B/C/D, one module per tier
- `validation/` — LOSO + expanding-window + metrics
- `inference/` — predict_season.py + explain.py + CLI
- `configs/run_config.yaml` — single source of truth per run
- `PROJECT_LOG.md` — running agent log, append-only, audit-grade

## Phase status

| Phase | Description | Status |
|---|---|---|
| 0 | Environment & scope setup | ✅ complete |
| 1 | Ground truth backbone | ✅ complete (2004 rows, 1956-2025, 100% cross-verified) |
| 2 | Data acquisition | ✅ complete (522 players with stats, 645 trophy rows) |
| 3 | Entity resolution | ✅ complete (532 resolved, 302 documented gaps, 0 unresolved) |
| 4 | Feature engineering | ✅ complete (2004 rows × 45 features across 7 families) |
| 5 | Modeling (Tiers A→D) | ✅ complete (Tier B selected as primary per Tier D decision rule) |
| 6 | Validation & calibration | ✅ complete (LOSO + expanding-window + one-shot held-out eval) |
| 7 | Inference + explanation | ✅ complete (CLI + JSON contract + per-candidate explanations) |
| 8 | Web handoff doc | ✅ complete (WEB_LAYER_HANDOFF.md — web app itself out of scope) |

## Key results

**Validation metrics (LOSO CV, 62 folds):**
- Tier A (heuristic): 32.3% top-1, 48.4% top-3
- Tier B (pairwise linear, **selected**): 33.9% top-1, 50.0% top-3, 0.410 Spearman
- Tier C (XGBoost): 35.5% top-1, 54.8% top-3, 0.404 Spearman

**Final held-out evaluation (one-shot, 7 seasons 2018-2025):**
- Tier A: 14.3% top-1, 42.9% top-3
- Tier B: 14.3% top-1, 14.3% top-3
- Tier C: 28.6% top-1, 28.6% top-3

**Per-era breakdown (LOSO CV, Tier B):**
- Classical (1956-1994): 23.1% top-1 (limited by 42% NaN features from bio-only Wikipedia pages)
- Pre-merger (1995-2009): 33.3% top-1
- FIFA merger (2010-2015): 83.3% top-1 (highly predictable)
- Post-split (2016-2017): 100.0% top-1

## Running

```bash
# Predict a season
python inference/predict_season.py --season 2024 --top-n 10

# Save prediction to JSON
python inference/predict_season.py --season 2024 --output pred.json

# Generate human-readable explanation
python inference/explain.py --input pred.json --top-n 5

# Run full validation (LOSO + expanding-window + held-out)
python validation/run_validation.py
```

## Reference documents

- `docs/specs/01_ARCHITECTURE_BLUEPRINT.md` — system design, schemas, dir structure
- `docs/specs/02_IMPLEMENTATION_PLAN.md` — phased build order with exit criteria
- `docs/specs/03_REQUIREMENTS.md` — data + tech stack requirements
- `docs/specs/04_KEY_FOCUS_AREAS.md` — 10 highest-risk silent-failure areas
- `PROJECT_LOG.md` — running audit log (1134 lines) covering every decision
- `WEB_LAYER_HANDOFF.md` — handoff doc for future web app build
- `reports/validation_report_2026-07-28.md` — full validation report
