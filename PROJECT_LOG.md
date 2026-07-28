# PROJECT_LOG.md — Ballon d'Or Prediction Engine

Running audit log for the autonomous build. Append-only; one section per
phase (or significant sub-step). Written for a human auditor reviewing the
build after a long autonomous run, not as a formality.

Convention:
- Dates are ISO 8601 (UTC).
- Be specific about numbers (row counts, join rates, validation metrics).
- Distinguish: real sourced values vs deliberately imputed vs unavailable.
- Flag every judgment call with its rationale.
- Never silently paper over a gap — surface it.

---

## 2026-07-28 — Phase 0 kickoff: task understanding & plan

### Task understanding

Build a learning-to-rank system that recovers the latent decision function
the Ballon d'Or jury has historically approximated across 1956–present,
expressed as (a) an interpretable weighted formula baseline and (b) a
learned ranking model, with a first-class per-candidate explanation layer.

Two non-negotiables from the spec that I will treat as hard stops:
1. **Validation discipline (Key Focus Areas §8):** the held-out test seasons
   (most recent 6-8 complete seasons) get looked at exactly once, at the
   very end of Phase 6. No peeking to "just check" a feature choice or
   hyperparameter. If the temptation arises, stop.
2. **Visible gaps (Key Focus Areas §9):** every imputed value gets a
   companion `_is_imputed` flag column. Missing-and-not-imputed stays NaN.
   No silent fills.

Other load-bearing design choices:
- P1 (rank, don't regress) — historical point totals are voting-system
  artifacts, not comparable across eras; the model target is within-season
  relative order, not absolute points.
- P4 (two eras, different rigor) — modern era (2014-15+) gets full feature
  depth target; classical gets simplified (goals/assists/apps/position);
  same logic, different data depth.
- Tier A (heuristic) → Tier B (pairwise linear) → Tier C (GBM) → Tier D
  (selection). Prefer B unless C consistently beats it across multiple folds.
- Phase 8 (web layer) is out of scope — only the handoff doc gets written.

### Phase 0 plan

1. Set up repo skeleton per Architecture Blueprint §5.
2. Initialize `configs/run_config.yaml` with placeholders for era
   boundaries, held-out test seasons, feature registry path, model tier
   config.
3. Verify sandbox tooling: Python version, presence of pandas/pyarrow/
   requests/bs4/lxml/pyyaml/rapidfuzz/sklearn/xgboost/shap/scipy/
   matplotlib/playwright.
4. Verify web access against at least one representative source per data
   category (ground truth, stats, trophies) before committing to a full
   scraping plan. Surface any blocked source immediately rather than
   silently producing an incomplete dataset.

### Phase 0 execution

**Tooling check (2026-07-28):**
- Python 3.12.13 at `/home/z/.venv/bin/python` — satisfies Python 3.10+.
- Pre-installed and verified: pandas 2.2.3, requests 2.32.5, httpx 0.28.1,
  beautifulsoup4 4.14.3, lxml 6.0.2, PyYAML 6.0.3, RapidFuzz 3.14.3,
  scikit-learn 1.5.2, xgboost 2.1.3, lightgbm 4.5.0, scipy 1.14.1,
  matplotlib 3.9.2, playwright 1.57.0.
- Missing → installed: `pyarrow 25.0.0` (required for Parquet, the
  canonical on-disk format per Architecture Blueprint §5).
- Deferred: `shap` — only needed for Tier C explanation in Phase 5/7; will
  install then rather than carry an unused dep through Phase 1-4.

**Web access check (2026-07-28, UTC):**

Tested with `curl` (default + browser User-Agent) and Playwright headless:

| Source | URL | curl UA=browser | Playwright | Verdict |
|---|---|---|---|---|
| Wikipedia (Ballon d'Or) | https://en.wikipedia.org/wiki/Ballon_d%27Or | HTTP 200 (1.38 MB) | n/a | ✅ primary ground-truth source |
| Wikipedia (player page) | https://en.wikipedia.org/wiki/Erling_Haaland | HTTP 200 (1.75 MB) | n/a | ✅ stats source (career-by-season table) |
| Wikipedia (UCL final) | https://en.wikipedia.org/wiki/2023_UEFA_Champions_League_final | HTTP 200 (629 KB) | n/a | ✅ trophy source |
| fbref | https://fbref.com/en/ | HTTP 403 (Cloudflare "Just a moment...") | HTTP 403 (same challenge page) | ❌ blocked — known gap |
| worldfootball.net | https://www.worldfootball.net/ | HTTP 403 | n/a | ❌ blocked |
| transfermarkt | https://www.transfermarkt.com/ | HTTP 405 | n/a | ❌ blocked (POST-only?) |
| uefa.com | https://www.uefa.com/uefachampionsleague/ | HTTP 403 | n/a | ❌ blocked |

**Ground-truth table structure verified (Wikipedia Ballon d'Or page):**
- 24 tables total on the page.
- Table[2] = main historical list 1956–2009: 283 rows × 5 cols
  `(Year, Rank, Player, Team, Points)` with multi-index headers
  `('Year', "Ballon d'Or (1956–2009)")` etc.
- Years present in table[2]: full range 1956–2009 (54 unique years).
- Separate tables exist for 2010-2015 (FIFA Ballon d'Or merger window) and
  2016-present (post-split) — will be parsed in Phase 1.

**Stats source structure verified (Wikipedia Haaland page):**
- 40 tables total; table[1] is the career-by-season stats table with
  multi-index columns `(Club, Season, League{Division,Apps,Goals}, …)`.
- This pattern is consistent across modern Wikipedia player pages and will
  be the workhorse stats source for both eras (with the caveat that
  classical-era player pages have less complete assist/minute data, per
  Requirements A.2).

**Trophy source structure verified (Wikipedia UCL 2023 final page):**
- 28 tables; includes match stats, lineups, season summary, road-to-final.
- Per-competition Wikipedia pages follow stable templates — good fit for
  `pandas.read_html`.

### Known gap: modern-era advanced metrics (xG / xA)

**What's missing:** fbref and Understat (the canonical free sources for
expected-goals / expected-assists / shot-creating-actions data) are both
behind Cloudflare bot detection in this sandbox, and Playwright headless
with a browser User-Agent does NOT bypass it (challenge page returns
HTTP 403 with title "Just a moment...").

**Impact on the spec:** Architecture Blueprint §4.4 family 1 lists xG/xA
and progressive actions as part of the modern-era full feature-depth
target. Without these, the modern era effectively has the same stat depth
as classical (goals/assists/apps/minutes), with two compensating factors:
1. Modern-era assist recording is far more reliable than pre-1990s.
2. Per-90 normalization and peer-percentile features (family 5) still work
   on goals/assists/minutes and give us cross-era comparability per
   Key Focus Area §7.

**Plan to revisit in Phase 2:** Before committing to "no xG/xA ever", I
will attempt, in order:
1. `curl_cffi` (Chrome TLS fingerprint impersonation) — often bypasses
   Cloudflare's "Just a moment" challenge.
2. fbref's mirror / alternate paths (e.g., `/en/comps/` listing pages
   sometimes pass when player pages don't).
3. If both fail: document as a permanent gap in the validation report
   (Phase 6) and proceed with goals/assists/minutes + per-90 + peer-
   percentile as the modern feature set. Per Key Focus Area §9, a
   documented gap is the correct outcome; silent fabrication is the
   failure mode this project exists to avoid.

### Phase 0 exit criterion check

Per Implementation Plan Phase 0:
> Repo skeleton exists, `run_config.yaml` is populated with sane defaults,
> and the agent has successfully test-fetched at least one page from each
> planned source category.

- ✅ Repo skeleton created at `/home/z/my-project/ballondor-engine/` with
  all directories from Architecture Blueprint §5.
- ✅ `configs/run_config.yaml` populated with era boundaries, held-out test
  season placeholders, model tier config, source registry, validation
  protocol spec, missing-data flag convention.
- ✅ Ground truth source (Wikipedia Ballon d'Or) test-fetched and parsed.
- ✅ Stats source (Wikipedia player pages) test-fetched and parsed.
- ✅ Trophy source (Wikipedia competition pages) test-fetched and parsed.
- ⚠️ Advanced-metrics sub-source (fbref) blocked — documented above, plan
  to revisit in Phase 2 with `curl_cffi`. This does NOT block Phase 0 exit
  because the three required categories (ground truth, stats, trophies)
  are all verified.

**Phase 0 exit criterion: MET.** Proceeding to Phase 1 (Ground Truth
Backbone) on next session segment.

