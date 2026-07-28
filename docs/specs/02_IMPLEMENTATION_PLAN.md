# Ballon d'Or Prediction Engine — Implementation Plan

This plan is written for execution by an autonomous coding agent operating
in long, mostly-unattended sessions. Each phase has an explicit **exit
criterion** — a concrete, checkable condition the agent must verify before
moving to the next phase. Do not proceed past a phase whose exit criterion
is unmet; instead, log the blocker and either resolve it or flag it clearly
for human review.

The agent should maintain `PROJECT_LOG.md` at the repo root throughout,
appending a dated entry after every phase (and after any significant
sub-step within a long phase) summarizing: what was attempted, what
succeeded, what failed, and what decisions were made and why. This log is
the primary artifact for a human to audit agent reasoning after a long
autonomous run, and should be treated as seriously as the code itself.

---

## Phase 0 — Environment & Scope Setup

**Tasks:**
- Set up repo structure per Architecture Blueprint §5
- Initialize `configs/run_config.yaml` with placeholders for: era boundary
  year, held-out test seasons, feature registry path, model tier to run
- Verify sandbox terminal has needed tooling (see Requirements doc) —
  install what's missing, log versions
- Confirm web access works for at least one representative source per
  data category (ground truth, stats, trophies) before committing to a
  full scraping plan

**Exit criterion:** Repo skeleton exists, `run_config.yaml` is populated
with sane defaults, and the agent has successfully test-fetched at least
one page from each planned source category.

---

## Phase 1 — Ground Truth Backbone

This is the highest-priority, highest-care phase — everything downstream
joins against this table, so errors here propagate silently everywhere.

**Tasks:**
1. Scrape/compile the full winner + top-5-nominee list for every Ballon
   d'Or edition from 1956 to the present, including:
   - Handling the Ballon d'Or Féminin separately if in scope (default:
     out of scope unless the user requests it — confirm assumption in
     log, do not silently include or exclude)
   - Correctly noting the 2020 cancellation (no award given — COVID) as
     a genuine gap, not a data error
   - Correctly handling the 2010–2015 FIFA Ballon d'Or merger window
   - Correctly handling the 2016+ Ballon d'Or split back to France
     Football only
2. Research and hard-code the **evaluation period** (calendar-year vs
   season-based, exact start/end) for every single year — cross-verify
   each year against at least two independent sources. This sub-task is
   explicitly allowed to be slow; accuracy here is worth the time cost.
3. Build `ground_truth.parquet` per the schema in Architecture Blueprint
   §4.1.
4. Run an internal consistency QA pass: every year has a winner, rank-1
   through rank-N are contiguous with no gaps, no duplicate players
   within a year, points (where available) are non-increasing with rank.

**Exit criterion:** `ground_truth.parquet` exists, passes the QA pass in
task 4 with zero unresolved anomalies, and a random 10-year sample has
been spot-checked by the agent against a second independent source with
results logged in `PROJECT_LOG.md`.

---

## Phase 2 — Data Acquisition (Individual Stats, Trophies, Narrative)

**Tasks:**
1. Build the modern-era stats scraper (2014–15 onward) — target the
   fullest available feature set per Architecture Blueprint §4.4 family 1.
2. Build the classical-era stats scraper (1956–2014) — simplified feature
   set per P4 (goals, assists, appearances, minutes where available).
3. Build the trophy/competition results scraper — league titles,
   continental cup outcomes, international tournament outcomes, by
   calendar year.
4. Build the narrative/media signal collector — this is explicitly
   best-effort and partially manual/agent-judgment-based (e.g., flagging
   "iconic final performance" seasons). Document clearly in
   `PROJECT_LOG.md` which narrative flags were agent-inferred vs sourced,
   since this feature family carries the most subjectivity risk.
5. Every scraper writes incrementally and idempotently to
   `data/raw/<source>/` keyed by `season_id + player_name_raw`, per
   Architecture Blueprint §4.2 requirement. Verify resumability by
   deliberately interrupting and re-running each scraper once during
   development.

**Exit criterion:** For every `(season_id, player)` pair in
`ground_truth.parquet`, at least the era-appropriate minimum feature set
exists in raw form, OR the gap is explicitly logged as a known missing
data point (never silently dropped — missing data must be visible
downstream, not invisible).

---

## Phase 3 — Entity Resolution

**Tasks:**
1. Build canonical player ID scheme and `alias_table.yaml` seed (agent
   should populate this by cross-referencing name variants encountered
   during Phase 2, not guess in advance).
2. Build fuzzy-match resolution pipeline with an explicit confidence
   threshold; anything below threshold gets logged to a review file
   rather than auto-resolved.
3. Agent self-reviews the low-confidence review file using available
   web search/VLM tooling as needed (e.g., checking a player photo or
   birth-year/club cross-reference to disambiguate) before finalizing.
4. Run `qa_report.py`: confirm every ground-truth row resolves to exactly
   one stats row per source table; surface unresolved rows explicitly.

**Exit criterion:** Zero unresolved ground-truth rows remain silently
unjoined — every row either successfully joins or is explicitly logged
as a documented gap with a stated reason.

---

## Phase 4 — Feature Engineering

**Tasks:**
1. Build `feature_registry.yaml` declaring every feature's era
   availability tag (`modern_only` / `all_eras` / `classical_proxy`) per
   Architecture Blueprint §4.4.
2. Implement each feature family as its own module under
   `features/feature_families/` — position-adjustment logic, per-90
   normalization, peer-percentile computation, recency weighting, trophy
   encoding, narrative flag integration.
3. Build `features.parquet` — the final joined feature matrix, one row
   per candidate-season, era tag attached.
4. Sanity-check distributions: plot/inspect a handful of known cases
   (e.g., a widely-agreed "obvious winner" season and a widely-agreed
   "controversial/surprising winner" season) and confirm the raw feature
   values match football-domain expectations before proceeding to
   modeling. This catches silent unit errors (e.g., per-90 vs per-season
   confusion) before they contaminate every downstream model.

**Exit criterion:** `features.parquet` built, feature registry complete,
and the sanity-check spot review in task 4 is logged with no
unexplained anomalies.

---

## Phase 5 — Modeling (Tiers A → D, in strict order)

**Tasks:**
1. **Tier A (baseline):** implement the explicit weighted-sum formula
   with manually reasoned starting weights. Run it against the full
   dataset, record baseline metrics. This is a non-learned reference
   point — do not skip it, even though it feels primitive; it is the
   floor every later model must beat.
2. **Tier B (pairwise linear):** implement pairwise logistic ranking,
   train, extract coefficients, sanity-check coefficient signs/magnitudes
   against football-domain intuition (e.g., trophy-win coefficient should
   be positive and non-trivial; a nonsensical sign on an important
   feature is a bug signal, not a "surprising finding," until proven
   otherwise).
3. **Tier C (GBM ranker):** implement XGBoost/LightGBM ranking objective
   with group-by-season, aggressive regularization defaults (shallow
   depth, small learning rate, early stopping). Do not tune hyperparameters
   against the final held-out test set — only against validation folds.
4. **Tier D (selection):** run the full validation protocol (Phase 6)
   against A, B, and C; select per the decision rule in Architecture
   Blueprint §4.5 (prefer B unless C shows consistent, non-marginal
   improvement across multiple folds).

**Exit criterion:** All three tiers trained and validated; a written
model-selection decision exists in `PROJECT_LOG.md` explaining which
model was chosen and why, with the comparison metrics that justified it.

---

## Phase 6 — Validation & Calibration

**Tasks:**
1. Implement LOSO cross-validation across every non-held-out season.
2. Implement expanding-window validation as the secondary, more
   realistic protocol.
3. Compute and log the full metric suite (top-1 accuracy, top-3/top-5
   hit rate, Spearman/Kendall correlation) per fold, aggregated, and
   split by era.
4. Only after all of the above: run the single final evaluation against
   the held-out test seasons (most recent 6-8 complete seasons, untouched
   until now). This is a one-shot check — do not iterate model design
   based on held-out results; if performance is poor here, that is a
   final, honestly reported finding, not a cue to keep tuning against it.
5. Produce a validation report (`reports/validation_report_<date>.md`)
   summarizing all of the above, including the feature-importance /
   coefficient interpretation required by Architecture Blueprint §4.6.

**Exit criterion:** Validation report exists, covers all required metrics
and both validation protocols, and includes an explicit, human-readable
discussion of where and why the model over/under-performs by era.

---

## Phase 7 — Inference Pipeline (Current Season Prediction)

**Tasks:**
1. Implement `predict_season.py`: given a candidate pool for a
   not-yet-decided season, compute features (reusing Phase 4 logic
   exactly — no parallel feature logic), run the selected model, output
   the JSON contract from Architecture Blueprint §4.7.
2. Implement `explain.py`: per-candidate contribution breakdown (direct
   coefficient contribution for the linear model; SHAP values if the GBM
   tier is selected).
3. Build a minimal CLI wrapper (`python -m ballondor predict --season 2026`
   or similar) that runs the full inference + explanation flow end to end.
4. **Manual spot check:** run the pipeline against the most recent
   completed season (already known outcome, but not used in training if
   it was part of the held-out set) and confirm the output "reads" as
   sensible to a football-literate reviewer, not just numerically
   plausible.

**Exit criterion:** CLI produces a complete, explained ranking for a
given season in a single command, and the spot-check output has been
reviewed and logged as sensible.

---

## Phase 8 — Documentation & Handoff for Web Layer (Phase 2 of product, later)

**Tasks:**
1. Ensure Stage 6's JSON output contract is stable and versioned —
   this is the only interface the future web layer should depend on.
2. Write a short `WEB_LAYER_HANDOFF.md` describing the JSON contract,
   how to invoke `predict_season.py` as a service call, and the scenario
   ("what-if I change this player's metric") interaction model, so a
   future build session (agent or human) can pick this up without
   re-deriving context.

**Exit criterion:** `WEB_LAYER_HANDOFF.md` exists and the JSON contract
is demonstrably stable (unchanged) across at least two independent
`predict_season.py` runs for different seasons.

---

## Phase Sequencing Notes

- Phases 1–4 (data) will consume the majority of wall-clock time and
  tool calls — this is expected and correct; the user's own framing
  identifies metric-set construction as "a major task."
- Do not parallelize Phase 5 (modeling) ahead of Phase 4 completing —
  a common failure mode is prematurely modeling on an incomplete/unstable
  feature matrix and then re-deriving everything when Phase 4 changes.
- Phases 6 and 7 depend on a *frozen* Phase 5 model choice — do not keep
  silently retraining during Phase 7 in response to spot-check
  dissatisfaction; if the spot check fails, that is a signal to return
  formally to Phase 5/6, log why, and redo validation, not to patch
  inference-time outputs directly.
- Web layer (Phase 8 handoff + actual future build) is explicitly
  out of scope for this build session per the user's stated CLI-first
  preference — do not begin implementing it beyond the handoff doc
  unless separately instructed.
