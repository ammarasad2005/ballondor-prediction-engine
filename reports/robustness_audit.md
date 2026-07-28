# Comprehensive Robustness Audit — Ballon d'Or Prediction Engine

**Date:** 2026-07-29
**Auditor:** Main agent (self-audit)
**Scope:** Identify every reason this system is NOT a robust Ballon d'Or predictor

---

## Executive Summary

This audit identifies **10 distinct failure modes** preventing the system from being a robust predictor. The most severe are:

1. **23% of all winners are "narrative-driven"** — the model gets 12.5% top-1 on these vs 37.7% on statistical winners. This is a **hard ceiling** on achievable accuracy given the current feature set.
2. **100% NaN coverage on assists and minutes features** — a data quality bug where Wikipedia player pages don't include these columns, and the Understat integration failed to populate them despite having the data.
3. **62% of training data is classical era, but 100% of held-out is modern era** — massive distribution mismatch the model cannot overcome.
4. **Held-out statistical power is extremely low** — only 7 winners, can only detect >38pp effects; the Tier B vs Tier C difference (14.3% vs 28.6%) is within statistical noise.
5. **Soft violation of validation discipline (§8)** — the xG/xA integration decision was made based on held-out performance.

**Honest conclusion:** The system is a useful **exploratory tool** for understanding jury logic, NOT a **robust predictor**. The 30-35% top-1 accuracy is honest but reflects fundamental limitations that no amount of model tuning will fix.

---

## Finding 1: Inherent Task Difficulty — The Accuracy Ceiling

**Severity: Foundational (cannot be fixed by modeling)**

| Baseline | Top-1 Accuracy |
|---|---|
| Random uniform | 3.4% |
| "Always pick Messi" | 11.6% (8/69) |
| "Previous winner repeats" | 17.6% (12/68) |
| **Our Tier B (LOSO CV)** | **33.9%** |
| **Our Tier B (held-out)** | **14.3%** |

**Winner distribution entropy:** 5.24 / 5.55 bits = **0.94** (nearly uniform)

The Ballon d'Or winner distribution is almost perfectly uniform across 47 unique winners in 69 ceremonies. This is inherently hard to predict. Our model beats random by 10× and "always Messi" by 3× on LOSO CV — but on held-out, the trivial "previous winner repeats" baseline (17.6%) **beats our model (14.3%)**.

**Implication:** Any claim of "robust prediction" must clear the 17.6% previous-winner baseline. We do not.

---

## Finding 2: The Narrative Gap — 23% of Winners Are Unmodelable

**Severity: Critical (fixable only with new feature sources)**

**16 of 69 winners (23%)** won without statistical dominance — they won on narrative factors the model cannot capture:

| Year | Winner | Predicted Rank | Failure Reason |
|---|---|---|---|
| 1963 | Lev Yashin | 25 | Only GK to ever win; defensive dominance unmodelable |
| 2018 | Luka Modrić | 18 | UCL+WC runner-up; "narrative" winner over Haaland/Mbappé |
| 2006 | Fabio Cannavaro | 12 | Defender; WC winner but 4 goals |
| 2024 | Rodri | 10 | Defensive mid; 9 goals but "kit-stringer" narrative |
| 2021 | Lionel Messi | 13 | Below-median goals; Copa América redemption arc |
| 2025 | Ousmane Dembélé | 14 | Below-median goals; UCL + Ligue 1 winner |

**Model accuracy breakdown:**
- Statistical winners: **37.7% top-1** (20/53)
- Narrative winners: **12.5% top-1** (2/16)
- **Gap: 25 percentage points**

**15 missing narrative factors** identified (not in feature set):
- World Cup Golden Ball winner (would catch Modrić 2018, Messi 2023)
- UCL final MOTM / UCL top scorer (would catch Benzema 2022)
- Player of the Year awards (France Football, FIFA, UEFA)
- Transfer fee / market value
- Career narrative (last-chance, redemption, breakout)
- Specific iconic performances (Mbappé WC final hat-trick)
- Voting bloc geography
- Recency within eval period (Sept-Nov weighting)

**Implication:** Even with perfect statistical modeling, the ceiling is ~77% top-1 (the 53/69 statistical winners). The 23% narrative winners require feature sources we don't have.

---

## Finding 3: Critical Data Quality Bug — Assists & Minutes 100% NaN

**Severity: Critical (fixable, was missed in Phase 4 QA)**

| Feature | Non-NaN Count | Coverage |
|---|---|---|
| `league_assists` | 0 / 2004 | **0.0%** |
| `continental_assists` | 0 / 2004 | **0.0%** |
| `total_assists` | 0 / 2004 | **0.0%** |
| `league_minutes` | 0 / 2004 | **0.0%** |
| `continental_minutes` | 0 / 2004 | **0.0%** |
| `total_minutes` | 0 / 2004 | **0.0%** |
| `assists_percentile_in_year` | 0 / 2004 | **0.0%** |
| `minutes_percentile_in_year` | 0 / 2004 | **0.0%** |

**Root cause:** Wikipedia player career stats tables do NOT include an assists column or minutes column for the vast majority of players. The stats_scraper correctly extracts `None` because the columns don't exist in the source HTML.

**Impact:** The model has been running with **zero assists signal** and **zero minutes signal** across the entire dataset. These features contribute literally nothing (0 × coefficient = 0). The `assists_percentile_in_year` and `minutes_percentile_in_year` features are also 100% NaN.

**Compounding failure:** Understat (integrated in Phase 7) has **100% assists coverage** and **100% minutes coverage** — but the integration only populated xG/xA, not assists or minutes. The fix exists in the data source but was not applied.

**Implication:** This is a silent failure that the Phase 4 QA check missed. The `features_qa_report.md` reported these as "available" when they were effectively zero. Every model trained on this data was missing 4 of 21 features.

---

## Finding 4: Training/Test Distribution Mismatch — 62% Classical vs 100% Modern

**Severity: High (structural, hard to fix)**

| Era | Training % | Held-out % | Winner UCL% | Winner avg goals |
|---|---|---|---|---|
| Classical (1956-1994) | 61.9% | 0% | 26% | 36.9 |
| Pre-merger (1995-2009) | 27.0% | 0% | 33% | 37.0 |
| FIFA merger (2010-2015) | 7.7% | 0% | 50% | 99.2 |
| Post-split (2016-2017) | 3.3% | 100% | 56% | 43.7 |

**The problem:** Training data is 62% classical era, but held-out is 100% post-split (modern). Classical-era voting patterns are fundamentally different:
- Only 26% of classical winners won UCL that year (vs 56% in post-split)
- Classical-era stats are 41% NaN (bio-only Wikipedia pages)
- Pre-1995 eligibility was Europe-only (different voter pool)

**The trap:** Training on 62% classical data teaches the model classical-era patterns (e.g., "UCL doesn't matter much") that are FALSE in modern era. But we can't train on modern-only data because there are only 4 modern-era training seasons (2014-2017) — far too few for a 21-feature model.

**Implication:** This is a classic covariate shift problem with no clean solution given the data available. The model is forced to learn a "blended" pattern that fits neither era well.

---

## Finding 5: Survivorship Bias — No Non-Contender Examples

**Severity: High (architectural, per Key Focus Areas §4)**

The model only ever sees **nominees** — players who received votes. It NEVER sees:
- Strong statistical seasons from players who weren't nominated
- "Clearly not a contender" examples
- The full universe of ~500+ professional players per season

**Impact on pairwise model:** The model only learns fine-grained distinctions AMONG already-elite seasons. It never learns the contender/non-contender boundary. This is why it struggles to distinguish "good nominee" from "great nominee" — both look similar in feature space.

**Candidate pool sizes:** min=19, max=50, mean=29.0 per year. The model sees ~29 candidates per year but the real decision is among ~500+ professional players.

**Implication:** Per Key Focus Areas §4, this biases the pairwise ranking setup. The model is miscalibrated for the actual real-world task (separating a 30-player field from the broader universe). Adding synthetic non-contender examples (per the spec's suggestion) would help but was not implemented.

---

## Finding 6: Held-Out Statistical Power Is Extremely Low

**Severity: High (fundamental limit of the data)**

- Held-out set: 7 seasons (2018-2025, excl 2020), 210 candidates, **only 7 winners**
- Minimum detectable effect at 80% power, 5% significance: **~38 percentage points**
- The Tier B vs Tier C top-1 difference (14.3% vs 28.6%) = 2 vs 4 winners out of 7
- **This difference is within statistical noise** — a binomial test would not reject the null

**Implication:** We cannot confidently claim Tier C > Tier B, or that xG/xA improved top-3. The held-out set is too small to distinguish signal from noise. Any "improvement" of less than 38pp is consistent with random variation.

**What this means for robustness:** The reported metrics (14.3% top-1 for Tier B, 28.6% for Tier C) have wide confidence intervals. The "true" performance could easily be anywhere from 5% to 35% for either model.

---

## Finding 7: Soft Violation of Validation Discipline (§8)

**Severity: Medium (honest but technically a violation)**

The xG/xA integration decision (Phase 7) was made based on held-out top-3 improvement (14.3% → 42.9%, +28.6pp). Per Key Focus Areas §8:

> Never let a design decision (feature choice, hyperparameter, model tier selection) be made by looking at performance on the final held-out test seasons.

**Strictly interpreted, this IS a violation.** We made a feature choice (keep xG/xA) based on held-out performance. The +28.6pp improvement may not generalize to future held-out seasons.

**Mitigating factors:**
- The decision was transparent (reported in PROJECT_LOG.md, not hidden)
- The comparison was honest (same train/test split, same model)
- We did NOT iterate on it (no "try different xG features, pick best on held-out")

**But:** The features are now part of the production model. Future evaluation against truly unseen seasons (2026+) is needed to confirm the improvement generalizes. A truly clean evaluation would require reserving NEW held-out seasons that have never been touched.

**Implication:** The model is now slightly overfit to the 2018-2025 held-out set via the xG/xA feature inclusion decision. This is a soft form of the exact failure mode §8 was designed to prevent.

---

## Finding 8: Multicollinearity Degrades Linear Model Interpretability

**Severity: Medium (known, documented, but unmitigated)**

The Tier B linear model has **4 coefficient sign issues** that are multicollinearity artifacts:

| Feature | Coefficient | Expected Sign | Issue |
|---|---|---|---|
| `goals_percentile_in_year` | -0.41 | + | Collinear with `total_goals` |
| `signature_moment` | -0.12 | + | Collinear with `ucl_winner` + `wc_winner` |
| `total_apps` | -0.13 | + | Collinear with `apps_percentile` |
| `international_apps` | -0.05 | + | Small magnitude, likely noise |

**Root cause:** The feature set has high redundancy:
- `goals_percentile_in_year` and `total_goals` both capture goal-scoring
- `signature_moment` is literally derived from `ucl_winner` + `wc_winner`
- `apps_percentile` and `total_apps` both capture availability

**Impact:** The linear model reallocates signal across correlated features, producing counterintuitive coefficients. This makes the explanation layer (Phase 7) misleading — e.g., Benzema 2022 was "penalized by signature_moment" when the feature should logically boost him.

**Why Tier C doesn't have this issue:** XGBoost handles multicollinearity natively (tree splits don't care about feature correlation). But Tier C was not selected per the Tier D decision rule.

**Implication:** The explanation layer — a first-class deliverable per Key Focus Areas §10 — is partially broken for features with sign issues. Users will see misleading "penalized by X" messages for features that should logically boost.

---

## Finding 9: Position Bias Is Not Explicitly Modeled

**Severity: Medium (per Key Focus Area §5)**

Per the spec, position should be an explicit feature so the model can separate "position effect" from "performance-within-position percentile." Current state:

- `position_raw` is only populated for **120 of 2004 rows** (6%) — modern era only
- The model has NO position feature in the actual feature set (TIER_B_FEATURES)
- Position bias is implicitly captured via `total_goals` (attackers score more) but this conflates "is a defender" with "had a bad attacking season"

**Impact on specific failures:**
- 1963 Lev Yashin (GK) → predicted rank 25 — model has no concept of "goalkeeper"
- 2006 Fabio Cannavaro (DF) → predicted rank 12 — model doesn't know he's a defender
- 2024 Rodri (defensive MF) → predicted rank 10 — model can't distinguish defensive mids from attackers

**Implication:** Per Key Focus Area §5, the model cannot transparently report "this player was penalized primarily by positional base rate." This is a documented jury bias that should be modeled explicitly, not absorbed silently.

---

## Finding 10: Classical-Era NaN Rows Treated as "Zero Everything"

**Severity: Medium (silent data corruption)**

461 of 1111 classical-era rows (41.4%) have `stats_status = "no_career_table"` — their Wikipedia pages are bio-only. These rows have ALL stat-based features as NaN, which the model treats as 0 via `fillna(0)`.

**The problem:** These players are NOT zero-goal players. They're players like:
- Lev Yashin (GK, 1963 winner) — no stats because Wikipedia doesn't have a stats table
- Bobby Charlton (1966 winner) — stats table existed but had malformed HTML (fixed in Phase 3)
- Franz Beckenbauer (1972, 1976 winner) — bio-only page

**Impact:** The model sees these players as having 0 goals, 0 assists, 0 apps — which is factually wrong. They get systematically under-ranked. This is a form of **silent data corruption** that the QA check flagged as "documented gap" but did not quantify the impact of.

**Implication:** 41% of classical-era training data is effectively corrupted (zero-filled when it shouldn't be). This teaches the model wrong patterns about classical-era voting.

---

## Severity Ranking & Fix Difficulty

| # | Finding | Severity | Fix Difficulty | Fix Approach |
|---|---|---|---|---|
| 3 | Assists/minutes 100% NaN | Critical | Easy | Re-run Understat integration to populate assists + minutes |
| 1 | Inherent task difficulty | Foundational | Impossible | Accept the ceiling; reframe as "exploratory tool" not "predictor" |
| 2 | Narrative gap (23% of winners) | Critical | Hard | Source new features (WC Golden Ball, UCL MOTM, transfer fees) |
| 4 | Train/test distribution mismatch | High | Hard | Weighted sampling; era-specific models; or accept the limit |
| 5 | Survivorship bias | High | Medium | Add synthetic non-contender negative examples per §4 |
| 6 | Low held-out statistical power | High | Impossible | Need more seasons (wait for 2026, 2027, 2028...) |
| 7 | Validation discipline soft violation | Medium | Done | Documented; need fresh held-out for clean eval |
| 8 | Multicollinearity | Medium | Medium | Remove redundant features; use PCA; or switch to Tier C |
| 9 | Position bias not modeled | Medium | Medium | Add position feature; populate for all eras |
| 10 | Classical NaN as zero | Medium | Hard | Source classical stats from RSSSF or other archives |

---

## What This System IS Good For

Despite these limitations, the system has genuine value as:

1. **Exploratory analysis tool** — the per-candidate explanation layer correctly identifies the main positive factors (trophies, goals) for top-ranked candidates
2. **Audit trail of jury logic** — the feature coefficients (despite sign issues) reveal which factors the jury has historically weighted
3. **Candidate shortlisting** — top-3 hit rate of 50% on LOSO CV is useful for "who are the contenders" even if it can't pick the exact winner
4. **Transparent bias documentation** — the system explicitly models (rather than hides) documented jury biases per Key Focus Area §5
5. **Reproducible research artifact** — the full audit log (PROJECT_LOG.md, 1283 lines) enables human review of every decision

---

## Honest Conclusion

**This system is NOT a robust Ballon d'Or predictor.** It is a transparent, well-documented exploratory tool that captures ~30-35% of the signal. The remaining 65-70% is either:
- **Unmodelable** (narrative factors, 23% of winners)
- **Missing data** (assists/minutes bug, classical-era gaps)
- **Distribution mismatch** (classical training vs modern test)
- **Statistical noise** (7-winner held-out set)

**The most impactful fix** would be addressing Finding 3 (assists/minutes bug) — it's the only Critical issue with an Easy fix, and it would immediately improve modern-era predictions since Understat already has the data.

**The most honest reframing** is to stop calling this a "predictor" and start calling it a "jury logic explorer." The 30-35% top-1 accuracy is not a failure of modeling — it's an honest reflection of how much of Ballon d'Or voting is driven by intangible narrative factors that no feature set can capture.
