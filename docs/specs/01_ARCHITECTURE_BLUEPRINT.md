# Ballon d'Or Prediction Engine — Architecture Blueprint

## 1. Project Statement

Build a learning-to-rank system that models the Ballon d'Or jury's revealed
preferences from historical voting outcomes (1956–present), then generalizes
to rank the current season's candidate pool. The system is not attempting
100% predictive accuracy — it is attempting to **recover the latent decision
function** the jury has historically approximated, expressed as an
interpretable, tunable scoring formula plus a learned ranking model, and to
expose *why* a player ranks where they do.

Two deliverables, same underlying pipeline:
1. A CLI/pipeline tool (data → features → model → ranked output + explanation)
2. A later web layer that surfaces the same pipeline interactively

## 2. Design Principles (non-negotiable constraints)

These principles resolve every architecture decision below. Refer back to
them whenever a new design choice is ambiguous.

- **P1 — Rank, don't regress.** The target is relative order within a
  season's candidate pool, not an absolute score. Historical point totals
  are voting-system artifacts (voter count, weighting scheme, and eligible
  voter pool all changed over time) and are not comparable across eras.
- **P2 — Generalize, don't memorize.** Every modeling choice must be
  evaluated on strictly held-out seasons. A formula that reproduces history
  perfectly but cannot justify itself on unseen seasons is a failure mode,
  not a success — this is explicitly the user's stated concern ("shouldn't
  be exactly rigid").
- **P3 — Interpretability first, complexity second.** Start with linear /
  pairwise-linear models with human-readable coefficients. Only add
  non-linear model capacity (gradient boosting) after the linear baseline
  is understood, and only keep it if it earns its complexity on held-out
  seasons.
- **P4 — Two eras, one philosophy, different rigor.** Modern era (2014–15
  onward, advanced metrics available) is the primary target and gets full
  feature depth. Classical era (1956–2014) is best-effort — simplified
  feature set (goals, assists, trophies, tournament-year flags, narrative
  proxies), used mainly to stress-test whether the model's core logic
  (trophies + individual output + narrative timing) holds across eras, not
  to hit high accuracy pre-2014.
- **P5 — Explicit bias handling, not silent bias.** Known jury biases
  (attacker overrepresentation, big-club/media-market bias, European
  competition bias, recency/narrative bias) must be *modeled as features*
  where they help predictive accuracy, and *documented as known
  limitations* where they are ethically or analytically uncomfortable to
  bake in further. The system should be able to report "this player was
  penalized/boosted mainly by feature X" — bias transparency is a
  deliverable, not an afterthought.
- **P6 — Agent-native build.** The build environment is a single
  multi-tool coding agent (web access, sandboxed terminal, VLM, long-horizon
  task execution). Architecture should lean into what that agent is good
  at: autonomous scraping with self-verification, iterative script-based
  data cleaning, and long unattended build sessions with checkpointed
  state — not manual human data entry.

## 3. High-Level System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        STAGE 0: SEASON SCOPE                     │
│  Ballon d'Or ground truth table (winner + full nominee list per   │
│  year, 1956–present) — the backbone all other data joins against  │
└───────────────────────────────┬────────────────────────────────────┘
                                 │
┌───────────────────────────────▼────────────────────────────────────┐
│                    STAGE 1: DATA ACQUISITION LAYER                 │
│  ┌────────────────┐ ┌────────────────┐ ┌───────────────────────┐  │
│  │ Ground Truth    │ │ Individual Stats│ │ Team/Trophy Outcomes │  │
│  │ Scraper         │ │ Scraper (era-   │ │ Scraper              │  │
│  │ (France Football│ │ dependent       │ │ (UCL/league/intl.    │  │
│  │  archives, Wiki)│ │  sourcing)      │ │  tournament results) │  │
│  └────────────────┘ └────────────────┘ └───────────────────────┘  │
│  ┌────────────────┐ ┌────────────────────────────────────────────┐│
│  │ Narrative/Media │ │ Advanced Metrics (modern era only:         ││
│  │ Signal Scraper  │ │ xG/xA, progressive actions, per-90 splits) ││
│  └────────────────┘ └────────────────────────────────────────────┘│
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  raw/*.jsonl + raw/*.csv (checkpointed)
┌───────────────────────────────▼────────────────────────────────────┐
│                 STAGE 2: ENTITY RESOLUTION & JOIN LAYER            │
│  Player name normalization · season/calendar-year alignment        │
│  (Ballon d'Or since 2010ish scores Aug–Jul-ish "season"; pre-2010   │
│  scored on calendar year — MUST be handled explicitly per era) ·   │
│  club/country mapping · duplicate/alias resolution                 │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  candidate_seasons.parquet (long table)
┌───────────────────────────────▼──────────────────────────────────────┐
│               STAGE 3: FEATURE ENGINEERING LAYER                    │
│  Raw stats → position-adjusted, peer-percentile, recency-weighted,  │
│  trophy-encoded, narrative-flagged feature matrix. Era-aware:        │
│  feature availability differs 1956–2013 vs 2014–present             │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  features.parquet (versioned schema)
┌───────────────────────────────▼──────────────────────────────────────┐
│                  STAGE 4: MODELING LAYER                            │
│  4a. Baseline heuristic scorer (explicit weighted formula)           │
│  4b. Pairwise linear ranker (interpretable coefficients)             │
│  4c. Gradient-boosted ranker (XGBoost rank:pairwise / rank:ndcg)     │
│  4d. Ensemble/selection logic — pick best generalizing model per     │
│      validation protocol, not best training-fit model                │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  model artifacts + metrics
┌───────────────────────────────▼──────────────────────────────────────┐
│              STAGE 5: VALIDATION & CALIBRATION LAYER                │
│  Leave-one-season-out CV · expanding-window time-series CV ·         │
│  held-out final test seasons (never touched until final report) ·   │
│  metric suite: top-1 accuracy, top-3/top-5 hit rate, Spearman/       │
│  Kendall rank correlation, calibration by era                        │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │
┌───────────────────────────────▼──────────────────────────────────────┐
│           STAGE 6: INFERENCE & EXPLANATION LAYER                    │
│  Current-season candidate pool → feature computation → ranked        │
│  output → per-candidate feature-contribution breakdown (why they     │
│  rank where they do) → CLI report / JSON API for web layer           │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │
┌───────────────────────────────▼──────────────────────────────────────┐
│          STAGE 7 (LATER): WEB APPLICATION LAYER                     │
│  Thin API wrapping Stage 6 output · season browser · "why" panel ·   │
│  scenario tool (manually vary a player's metrics, see rank shift)    │
└────────────────────────────────────────────────────────────────────┘
```

## 4. Component Detail

### 4.1 Ground Truth Backbone

The single most important table in the project — every join anchors to it.

**Schema: `ground_truth.parquet`**
| column | type | notes |
|---|---|---|
| `season_id` | str | e.g. `"2023"` — canonical year label used by France Football for that ceremony |
| `award_year` | int | calendar year the ceremony was held |
| `eval_period_start` | date | start of the period the jury actually evaluated (varies by era — see below) |
| `eval_period_end` | date | end of the period the jury actually evaluated |
| `rank` | int | 1 = winner, 2..N = nominee finishing position |
| `player_name_raw` | str | as it appears in source |
| `player_name_canonical` | str | resolved canonical name (post entity-resolution) |
| `club_at_time` | str | primary club during eval period |
| `nation_team` | str | |
| `points` | float, nullable | raw voting points if available — **not used as a model target**, retained for reference/QA only |
| `source` | str | which scrape this row came from |

**Critical era-boundary handling (must be built explicitly, not assumed):**
- 1956–2006ish: award scored on **calendar year** performance
- 2007–2015: transitional; also merged with FIFA World Player of the Year
  2010–2015 (two competing awards existed briefly — the "Ballon d'Or" in
  this window is the merged FIFA Ballon d'Or; document this in metadata)
- 2016–present (excluding the COVID-cancelled 2020 edition, and the 2024
  rules change to full **season** (Aug–Jul) evaluation window): season-based
  eval period
- **Action item for the agent:** research and hard-code the exact eval
  window per year — do not assume a uniform rule. This table is small
  (~69 rows of year-level metadata) and should be manually verified against
  at least two independent sources per year, since it is the join key for
  everything else.

### 4.2 Data Acquisition Layer

Each scraper is an independent, idempotent, checkpointed script. Design
requirement: **every scraper must be re-runnable without re-fetching
already-successful rows** (write to disk incrementally, key on
`season_id + player_name_raw`, skip existing keys on rerun). This matters
because the agent operates in long-horizon sessions and scraping 69 years
of data across multiple sources will hit rate limits, transient failures,
and site structure changes — the pipeline must tolerate partial completion
and resumption without human intervention.

Candidate source types (agent should verify current availability/ToS at
build time, not assume these are all scrapable — see Requirements doc):
- Historical ballot/nominee lists: Wikipedia Ballon d'Or pages (well
  structured, good starting point), France Football archives
- Player statistical records: era-appropriate source per note in P4 —
  modern era wants shot-creation/xG-grade data, classical era typically
  only has goals/assists/appearances reliably
- Trophy/competition results: competition-by-year winner tables (also
  well-suited to Wikipedia's structured tables)
- Team league position and points at season end
- International tournament results in relevant calendar years

### 4.3 Entity Resolution & Join Layer

The single highest-risk-of-silent-bugs component. Name mismatches
(diacritics, transliteration differences, nicknames, mid-career club
changes, players who share names) will silently corrupt joins if not
handled deliberately.

Requirements:
- Canonical player ID scheme (agent should mint stable IDs, e.g. slugified
  name + birth year, and maintain an explicit alias table)
- Explicit alias/override table for known hard cases (accented characters,
  "Ronaldo" (the original Brazilian) vs "Cristiano Ronaldo" vs "Ronaldinho",
  club naming changes over decades, national team renames)
- Automated fuzzy-match candidate generation + **mandatory manual/agent
  self-review pass** on any match below a similarity confidence threshold
  — do not silently accept low-confidence matches
- QA step: after joining, verify row counts — every `ground_truth` row
  must resolve to exactly one stats row per source; log and surface any
  that don't, do not drop silently

### 4.4 Feature Engineering Layer

Organized into feature families. See `04_KEY_FOCUS_AREAS.md` for the
detailed rationale on why each family matters and known pitfalls.

**Feature families:**
1. **Individual production** (position-adjusted, per-90 normalized)
2. **Trophy/team success** (categorical + continuous encodings)
3. **International tournament boost** (calendar-year flag + performance)
4. **Availability/durability** (minutes, games, injury gaps)
5. **Peer-relative standing** (percentile within that year's candidate
   pool — critical for cross-era comparability, since raw stat magnitudes
   drift with football's evolving pace/style)
6. **Recency-weighted form** (intra-season half/quarter split with
   second-half overweighting — proxy for jury recency bias)
7. **Narrative/media signal** (best-effort, partially manual-flagged:
   signature-moment indicator, market-size/club-prestige proxy)

**Design requirement:** every feature must declare its **era
availability** (`modern_only`, `all_eras`, `classical_proxy_available`)
in a feature registry file (`feature_registry.yaml`), so the modeling
layer can automatically select the right feature subset per era without
ad hoc conditionals scattered through code.

### 4.5 Modeling Layer

Four model tiers, built in order, each a checkpoint the agent evaluates
before proceeding to the next (do not jump straight to the most complex
model):

**Tier A — Explicit weighted-sum baseline.** A fully transparent formula:
`score = Σ w_i * feature_i` with manually reasoned starting weights (not
learned) reflecting football-domain understanding of what the jury has
said it values. This is the sanity-check floor — nothing more complex is
worth keeping if it can't beat this.

**Tier B — Pairwise linear ranker.** Learn `w_i` via pairwise logistic
regression over within-season pairs (`did player A rank above player B`).
Fully interpretable coefficients — this is likely the primary deliverable
model given the interpretability requirement and small-N dataset.

**Tier C — Gradient-boosted ranker.** XGBoost/LightGBM with
`rank:pairwise` or `rank:ndcg` objective, `group` set per season. Higher
capacity, higher overfitting risk given N≈300-350 candidate-seasons —
must be regularized aggressively (shallow trees, strong L1/L2, small
learning rate, early stopping on validation seasons).

**Tier D — Model selection / light ensembling.** Compare B and C (and A
as floor) on the validation protocol below. Prefer B unless C shows a
consistent, non-marginal improvement across *multiple* validation folds
(not just one lucky split). A simple average-of-ranks ensemble of B+C is
an acceptable final choice if both contribute independently useful signal.

### 4.6 Validation & Calibration Layer

This layer enforces P2 (generalize, don't memorize) and is not optional
or deferrable — it must exist before any model result is trusted.

- **Leave-one-season-out (LOSO) cross-validation** as the primary
  protocol: train on all seasons except one, predict the held-out
  season's full ranking, repeat for every season.
- **Expanding-window validation** as a secondary, more realistic protocol:
  train only on seasons *before* year Y, predict year Y — this simulates
  the actual real-world use case (predicting a season you don't have
  future data for) and should be the deciding protocol when it disagrees
  with LOSO.
- **Metrics tracked per fold and aggregated:**
  - Top-1 accuracy (did the model's #1 pick match the actual winner)
  - Top-3 and top-5 hit rate (softer, more informative given small N)
  - Spearman's rho / Kendall's tau between predicted and actual order
    across the full nominee list
  - Per-era breakdown of all the above (classical vs modern) — do not
    report a single blended number, since P4 means performance should
    differ by design
- **Final held-out test set:** reserve the most recent 6-8 complete
  seasons, never used in any training or hyperparameter tuning decision
  until the single final evaluation pass.
- **Reporting requirement:** the validation report must include a
  feature-importance / coefficient summary alongside accuracy numbers —
  a model that "works" but whose top feature is an obvious leakage proxy
  (e.g. "won Ballon d'Or last year") must be caught here.

### 4.7 Inference & Explanation Layer

For a live/current season prediction, the output contract is not just a
ranked list — it must include a **per-candidate explanation**: which
feature families contributed most to that player's rank, expressed in
plain language derived from the model's actual coefficients/SHAP-style
contributions (for tree models, use SHAP; for the linear model, direct
coefficient × feature-value contribution is sufficient and preferable for
interpretability).

**Output contract (JSON, versioned schema):**
```json
{
  "season_id": "2026",
  "generated_at": "...",
  "model_version": "...",
  "rankings": [
    {
      "rank": 1,
      "player": "...",
      "score": 0.0,
      "top_contributing_features": [
        {"feature": "ucl_winner", "contribution": 0.31},
        {"feature": "goals_per90_percentile", "contribution": 0.22}
      ]
    }
  ]
}
```

### 4.8 Web Application Layer (Phase 2 — later)

Deferred to after the CLI/pipeline is validated. Thin design:
- Backend: simple API wrapping Stage 6's JSON output contract directly —
  no reimplementation of modeling logic in a second language/service
- Frontend: season browser, per-player explanation panel, and a
  "scenario" tool that lets the user manually adjust a candidate's
  feature values and see the rank shift live (directly useful for the
  user's stated interest in "varying the values of the metrics")
- No new modeling logic should live in the web layer — it is a
  presentation layer over Stage 6 only, to avoid drift between CLI and
  web results

## 5. Directory / Repo Structure (recommended)

```
ballondor-engine/
├── data/
│   ├── raw/                  # scraper output, one subfolder per source, checkpointed
│   ├── interim/               # post entity-resolution, pre-feature-engineering
│   └── processed/             # final features.parquet, ground_truth.parquet
├── scrapers/
│   ├── ground_truth_scraper.py
│   ├── stats_scraper_modern.py
│   ├── stats_scraper_classical.py
│   ├── trophy_scraper.py
│   └── narrative_flagger.py
├── entity_resolution/
│   ├── alias_table.yaml
│   ├── resolve.py
│   └── qa_report.py
├── features/
│   ├── feature_registry.yaml
│   ├── build_features.py
│   └── feature_families/     # one module per family in 4.4
├── models/
│   ├── tier_a_baseline.py
│   ├── tier_b_linear_ranker.py
│   ├── tier_c_gbm_ranker.py
│   └── model_selection.py
├── validation/
│   ├── loso_cv.py
│   ├── expanding_window_cv.py
│   └── metrics.py
├── inference/
│   ├── predict_season.py
│   └── explain.py
├── reports/                   # generated validation reports, per-run
├── configs/
│   └── run_config.yaml        # single source of truth for a pipeline run
├── PROJECT_LOG.md             # agent-maintained running log — see Implementation Plan
└── README.md
```

## 6. Key Technical Risks (architecture-level, expand in Key Focus Areas doc)

1. Era-boundary and eval-window mismatches silently corrupting the
   ground truth backbone
2. Name entity resolution errors silently corrupting joins
3. Overfitting on N≈300-350 rows if Tier C model isn't aggressively
   regularized and validated on the correct protocol
4. Feature leakage (features that encode the outcome, directly or via
   proxy) inflating validation metrics falsely
5. Survivorship/selection bias — the dataset only contains top-5
   finishers, not the full candidate pool the jury actually considered,
   which biases what "negative" examples look like in the pairwise
   ranking setup (mitigation strategy detailed in Key Focus Areas doc)
