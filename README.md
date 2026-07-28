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
| 0 | Environment & scope setup | in progress |
| 1 | Ground truth backbone | pending |
| 2 | Data acquisition | pending |
| 3 | Entity resolution | pending |
| 4 | Feature engineering | pending |
| 5 | Modeling (Tiers A→D) | pending |
| 6 | Validation & calibration | pending |
| 7 | Inference + explanation | pending |
| 8 | Web handoff doc | pending (out of scope for this build) |

## Running

Phase 0+ — environment setup only at this point. Once Phase 7 lands:

```bash
python -m ballondor predict --season 2026
```

Outputs the JSON contract from Architecture Blueprint §4.7 (ranked candidates
with per-candidate top-contributing-features breakdown).

## Reference documents

- `upload/01_ARCHITECTURE_BLUEPRINT.md` — system design, schemas, dir structure
- `upload/02_IMPLEMENTATION_PLAN.md` — phased build order with exit criteria
- `upload/03_REQUIREMENTS.md` — data + tech stack requirements
- `upload/04_KEY_FOCUS_AREAS.md` — 10 highest-risk silent-failure areas
