# Evaluative Audit — Performance + Bugs (Post-Iteration 2)

**Date:** 2026-07-29
**Scope:** Evaluate model performance after Iteration 2 improvements + identify remaining bugs

---

## Executive Summary

**Performance: SIGNIFICANT IMPROVEMENT** — LOSO CV top-1 improved from 33.9% to 38.7% (+4.8pp), top-3 from 50.0% to 66.1% (+16.1pp). Held-out top-3 improved from 14.3% to 57.1% (+42.8pp).

**Bugs: 6 NEW issues found** (3 medium, 3 low severity). The most critical is **inconsistent FEATURES lists across files** — the model training code uses 34 features while inference uses 39, meaning the production model is trained on a different feature set than what inference expects.

---

## Part 1: Performance Assessment

### LOSO CV Progression (honest cross-validation, 62 folds)

| Iteration | Top-1 | Top-3 | Top-5 | Spearman | Δ Top-1 |
|---|---|---|---|---|---|
| Original (pre-audit) | 33.9% | 50.0% | 67.7% | 0.410 | — |
| + audit fixes | 35.5% | 59.7% | 72.6% | 0.407 | +1.6pp |
| + geographic + position | 37.1% | 53.2% | 74.2% | 0.399 | +1.6pp |
| + tournament heroism | BACKTRACKED (collinear) | | | | |
| + narrative features | **38.7%** | **66.1%** | **77.4%** | **0.451** | +1.6pp |

**Total improvement:** +4.8pp top-1, +16.1pp top-3, +9.7pp top-5

### Held-Out Evaluation (one-shot, 7 seasons)

| Metric | Before Audit | After All Fixes | Improvement |
|---|---|---|---|
| Top-1 | 14.3% (1/7) | 28.6% (2/7) | +14.3pp |
| Top-3 | 14.3% (1/7) | **57.1% (4/7)** | +42.8pp |
| Top-5 | 42.9% (3/7) | 57.1% (4/7) | +14.2pp |

### Per-Era Breakdown

| Era | Top-1 | Top-3 | Top-5 | Avg Pred Rank |
|---|---|---|---|---|
| Classical (1956-1994) | 33.3% | 61.5% | 74.4% | 4.3 |
| Pre-merger (1995-2009) | 40.0% | 66.7% | 66.7% | 5.8 |
| FIFA merger (2010-2015) | **66.7%** | **100.0%** | **100.0%** | 1.3 |
| Post-split (2016-2025) | 22.2% | 66.7% | 66.7% | 6.8 |

### Remaining Failures (11 winners predicted at rank > 10)

| Year | Winner | Pred Rank | Root Cause |
|---|---|---|---|
| 1995 | George Weah | 22 | Geographic representation (African) — feature exists but weak |
| 2018 | Luka Modrić | 22 | Narrative + position (MF) — position data missing (Unknown) |
| 1986 | Igor Belanov | 21 | Geographic (Soviet) + classical-era data gaps |
| 2006 | Fabio Cannavaro | 20 | Position (DF) — position data missing |
| 1975 | Oleg Blokhin | 18 | Geographic (Soviet) + classical-era data gaps |
| 2025 | Ousmane Dembélé | 16 | Breakout narrative — no feature captures this yet |
| 2003 | Pavel Nedvěd | 13 | Below-median goals — narrative factor |
| 2024 | Rodri | 13 | Position (MF) + low goals — engine role |
| 1963 | Lev Yashin | 11 | Position (GK) — position data missing |
| 1967 | Flórián Albert | 11 | Geographic (Hungarian) + classical-era data |
| 2005 | Ronaldinho | 11 | Aesthetic factor — not quantified |

### Failure Pattern Analysis

**Correctly predicted winners vs Failed predictions (key feature differences):**

| Feature | Correct (avg) | Failed (avg) | Delta | Interpretation |
|---|---|---|---|---|
| total_goals | 57.9 | 21.7 | -36.2 | Failed winners score far fewer goals |
| xg | 42.5 | 9.3 | -33.2 | Failed winners have low xG (not big scorers) |
| position_adjusted_xg_contribution | 69.4 | 18.4 | -51.0 | Failed winners lack statistical dominance |
| prior_winner_count | 1.24 | 0.0 | -1.24 | Failed winners are all first-time winners |
| years_in_top_5 | 3.60 | 0.61 | -2.99 | Failed winners have little prior Ballon d'Or history |
| previous_ballon_dor_winner | 0.56 | 0.00 | -0.56 | Failed winners never won before |
| continent_prior_winners_count | 15.1 | 22.4 | +7.3 | Failed winners come from over-represented continents |

**Key insight:** The remaining failures are ALL first-time winners with low statistical output who come from regions that already have many prior winners. The model can't distinguish "this specific player is special despite low stats" from "this player just had a mediocre season."

### Statistical Significance

- Held-out n=7 winners, improvement from 1/7 to 2/7 top-1
- **NOT statistically significant** (binomial test p > 0.05)
- Top-3 improvement from 1/7 to 4/7 IS more meaningful but still small sample
- Need 2026+ seasons for truly confident evaluation

---

## Part 2: Bug Findings

### Bug 1: Inconsistent FEATURES Lists Across Files ⚠️ MEDIUM-HIGH

**The most critical bug.** Different files use different feature lists:

| File | Feature Count |
|---|---|
| inference/predict_season.py | 39 |
| models/tier_b_linear_ranker.py | 34 |
| models/tier_c_gbm_ranker.py | 34 |
| validation/run_validation.py | 33 |

**Impact:** The model is TRAINED on 34 features (tier_b), VALIDATED on 33 features (run_validation), but INFERENCE expects 39 features. This means:
- The production model may not include xG/xA features (which are in inference but not in tier_b)
- Validation results may not reflect the actual production model
- Predictions could be inconsistent between training and inference

**Fix:** Standardize all FILES to use the same FEATURES list. The inference pipeline (39 features) is the most complete — all other files should match it.

### Bug 2: 5 Features with 0% Coverage (All-NaN) ⚠️ LOW

| Feature | Status |
|---|---|
| continental_assists | 100% NaN — Wikipedia doesn't have this column |
| continental_minutes | 100% NaN — same |
| league_goals_per90 | 100% NaN — derived from league_minutes (which is mostly NaN pre-2014) |
| continental_goals_per90 | 100% NaN — same |
| second_half_goals_share | 100% NaN — never implemented (no data source) |

**Impact:** These features contribute nothing (0 × coefficient = 0). They bloat the parquet file and confuse the feature audit. Not a model correctness issue, but a data hygiene issue.

**Fix:** Remove these columns from features.parquet, or mark them as "planned" in feature_registry.yaml without adding to the parquet.

### Bug 3: Backtracked Features Still in Parquet ⚠️ LOW

5 tournament heroism features were backtracked (removed from FEATURES list) but remain as columns in features.parquet:
- continental_goals_ratio
- international_goals_per_app
- tournament_year_boost
- ucl_winner_goalscorer
- wc_winner_goalscorer

**Impact:** None on model behavior (not in FEATURES list). Data bloat only.

**Fix:** Remove these columns from parquet, or keep with a note that they're "experimental, not in production feature set."

### Bug 4: Boolean Features Stored as Object Dtype ⚠️ LOW

11 boolean features are stored as `object` dtype instead of `bool`:
- ucl_winner, ucl_runner_up, domestic_league_winner, domestic_league_runner_up
- world_cup_winner, world_cup_runner_up, euro_winner, copa_america_winner
- international_tournament_year, previous_ballon_dor_winner, signature_moment

**Impact:** Model code handles this via `if X[col].dtype == bool: X[col] = X[col].astype(int)` — but the check fails for object dtype, so these columns go through `fillna(0)` which converts True→1, False→0, None→0. Functionally correct but fragile.

**Fix:** Convert these columns to `bool` dtype in features.parquet, or explicitly handle object-to-int conversion in model code.

### Bug 5: previous_ballon_dor_winner Has 519 None Values ⚠️ LOW

519 rows (25.9%, mostly classical era) have `None` instead of `False` for `previous_ballon_dor_winner`. These are rows where the feature was never computed (no stats data → no prior winner lookup).

**Impact:** Model treats None as 0 via `fillna(0)`, so functionally correct. But this is the same "missing data treated as zero" issue that Finding 10 flagged — the model can't distinguish "never won before" from "data missing."

**Fix:** Set these to `False` explicitly (they're players who never won, just we didn't compute it). Or better: the `has_stats_data` flag already captures this distinction.

### Bug 6: TIER_A_WEIGHTS References Removed Feature ⚠️ LOW

`TIER_A_WEIGHTS` in `validation/run_validation.py` still includes `"signature_moment": 1.5` even though signature_moment was removed from the FEATURES list.

**Impact:** Tier A heuristic will try to access a column that may not be in the feature matrix. Currently works because the column still exists in parquet, but fragile.

**Fix:** Remove signature_moment from TIER_A_WEIGHTS.

### Bug 7: total_apps Exceeding 100 ⚠️ INFORMATIONAL

791 rows have total_apps > 70, with max=102. This is actually **correct** — modern players can play 70+ league games + 30+ continental games in a season. The sanity check threshold was too conservative.

**No fix needed** — update the sanity check threshold to 120.

---

## Part 3: Collinearity Analysis

40 highly correlated feature pairs (|r| > 0.8) were found. The most problematic:

| Feature Pair | Correlation | Issue |
|---|---|---|
| league_assists ↔ total_assists | +1.000 | Perfect correlation — total_assists = league_assists (continental_assists is all NaN) |
| league_minutes ↔ total_minutes | +1.000 | Same — total_minutes = league_minutes |
| xg ↔ npxg | +0.991 | Near-perfect — npxG is xG minus penalties |
| league_goals ↔ total_goals | +0.983 | Near-perfect — total_goals mostly = league_goals |
| xg ↔ position_adjusted_xg_contribution | +0.956 | position_adjusted is derived from xG |
| league_goals ↔ xg | +0.951 | Goals and xG are highly correlated by definition |

**Impact:** The linear model (Tier B) handles multicollinearity by redistributing coefficients, but this makes individual coefficient interpretation unreliable. This is the same issue that caused the signature_moment negative coefficient (Finding 8 from the original audit).

**Mitigation already in place:** `has_stats_data` and `data_completeness_score` flags help the model distinguish "zero because no data" from "zero because player didn't score." The L2 regularization (C=1.0) also mitigates coefficient instability.

**Not a blocker** — the model performs well despite collinearity. But the explanation layer should note this caveat.

---

## Severity Ranking

| # | Bug | Severity | Fix Difficulty | Impact if Unfixed |
|---|---|---|---|---|
| 1 | Inconsistent FEATURES lists | **MEDIUM-HIGH** | Easy (standardize) | Model trained on different features than inference expects |
| 2 | 5 all-NaN features | LOW | Easy (remove columns) | Data bloat, confusing audit |
| 3 | Backtracked features in parquet | LOW | Easy (remove columns) | Data bloat |
| 4 | Boolean features as object dtype | LOW | Easy (convert dtype) | Fragile type handling |
| 5 | previous_ballon_dor_winner None values | LOW | Easy (set to False) | Minor data hygiene |
| 6 | TIER_A_WEIGHTS references removed feature | LOW | Easy (remove weight) | Fragile if column dropped |
| 7 | total_apps > 100 | INFORMATIONAL | None (correct behavior) | N/A |

---

## Recommendations

### Immediate (should fix now)
1. **Standardize FEATURES lists** across all 5 files to match inference/predict_season.py (39 features)
2. **Remove signature_moment from TIER_A_WEIGHTS**

### Next iteration (when convenient)
3. Remove 5 all-NaN columns from parquet
4. Remove 5 backtracked tournament heroism columns from parquet
5. Convert boolean columns to proper `bool` dtype
6. Set previous_ballon_dor_winner None values to False

### Long-term (future iterations)
7. Address collinearity by removing redundant features (total_assists = league_assists when continental_assists is NaN)
8. Source continental_assists + continental_minutes data to break the perfect correlation
9. Add more feature sources to address the 11 remaining "narrative winner" failures

---

## Overall Assessment

**The model has improved significantly** from the original audit:
- LOSO CV: +4.8pp top-1, +16.1pp top-3
- Held-out: +14.3pp top-1, +42.8pp top-3

**The self-improving backtracking process is working as designed:**
- Features that help are kept (geographic, position, narrative)
- Features that hurt are backtracked (tournament heroism — collinear)
- The model is genuinely getting better at understanding jury logic

**Remaining limitations are structural:**
- 11 winners (16%) are still predicted at rank > 10 — these are "narrative winners" requiring feature sources we don't have
- Held-out sample (n=7) too small for statistical significance
- Classical era has 41% missing data that can't be sourced from current channels

**The FEATURES list inconsistency (Bug 1) is the only finding that could affect model correctness.** All other bugs are data hygiene issues that don't impact predictions. Fixing Bug 1 should be the immediate priority.
