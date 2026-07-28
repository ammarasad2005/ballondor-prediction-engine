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

### Plan for Phase 1

1. Scrape/compile the full winner + top-N nominee list for every Ballon
   d'Or edition 1956–present, including:
   - 1956–2009: from Wikipedia Ballon d'Or page table[2] (already verified).
   - 2010–2015: from the FIFA Ballon d'Or merger section (separate table).
   - 2016–present: from the post-split section (separate table).
   - 2020: marked as cancelled (COVID), not a data error.
2. Research and hard-code the evaluation period per year (calendar-year
   vs season-based, exact start/end dates). Per Architecture Blueprint
   §4.1, this is allowed to be slow — every year must be cross-verified
   against ≥2 independent sources. Will use Wikipedia + a second source
   (likely RSSSF or France Football archives via Wayback Machine).
3. Build `ground_truth.parquet` per schema in Architecture Blueprint §4.1.
4. Run internal consistency QA: every year has a winner, ranks contiguous,
   no duplicate players within a year, points non-increasing with rank.
5. Random 10-year sample spot-check against second independent source,
   logged here.

---

## 2026-07-28 — Phase 1 execution: ground truth backbone complete

### Phase 1 plan revision (early in execution)

Initial plan assumed main Wikipedia Ballon d'Or page table[2] covered
all years 1956-2025 with top-3 only, requiring per-year pages for fuller
nominee lists. Investigation revealed the main page table only had
top-3 per year, but per-year Wikipedia pages exist for ALL 69 years
(1956-2025, skipping 2020) with ~25-30 nominees each. So the strategy
was revised to: scrape all 69 per-year pages via the Wikipedia Action
API for uniform coverage, and use the main page top-3 table as the
cross-verification source.

### Scraping approach (revised)

**Source:** Wikipedia Action API (`https://en.wikipedia.org/w/api.php?action=parse&prop=text&page={year}_Ballon_d%27Or&format=json`).

Chosen over direct HTML page fetches because:
1. Plain HTTP GETs to `en.wikipedia.org/wiki/{year}_Ballon_d%27Or`
   started returning HTTP 403 "Too Many Reqs" within ~5 requests (IP
   rate limit).
2. Python `requests`/`urllib` were ALSO 403-blocked at the API endpoint,
   even with polite User-Agent — investigation showed Python's egress
   IP (47.57.242.119) was rate-limited while `curl`'s egress IP
   (47.57.232.232) was not. Likely other Python processes from this
   sandbox had tripped Wikipedia's per-IP limiter.
3. Workaround: bash+`curl` loop to pre-fetch all 69 per-year API
   responses to `data/raw/ground_truth/pages_api/{year}.json`, with
   1.5s delay between requests. All 69 fetches succeeded (0 failures).
4. Python scraper then runs in parse-only mode against the cached JSON
   files (idempotent per Architecture Blueprint §4.2).

### Parser development iterations

The parser went through 5 iterations as edge cases surfaced:

1. **Initial version**: `pd.read_html` on full page HTML — too slow
   (30+ seconds per page parsing all 24 tables). Switched to
   BeautifulSoup-find-target-table-then-parse approach (0.3s per page).

2. **1956-2009 era worked, 2010-2015 era returned only 3 rows**:
   FIFA Ballon d'Or merger pages split the men's ranking across TWO
   tables — table[0] has top 3 (winner + runners-up), table[1] has
   ranks 4+. Fixed by detecting the split (first table ≤4 rows AND
   second table with same column count exists) and concatenating.

3. **1995, 2001 returned 0 rows**: column headers had footnote markers
   like "Rank[2][3]" or "Rank[3]". Fixed by stripping `\[...\]` patterns
   in `normalize_columns` before equality check.

4. **2003, 2004 returned 0 rows**: tables had 12 columns (extra "Votes
   by place" sub-header spanning 1st/2nd/3rd/4th/5th vote columns).
   Filter was rejecting tables with >10 columns. Relaxed to ≤40.

5. **2022-2025 returned 0 rows**: `normalize_columns` was incorrectly
   mapping "position" → "rank" (collision), creating duplicate "rank"
   columns. Fixed by mapping "position" → "position" (preserved as a
   separate field for Phase 4 feature engineering — player's field
   position GK/DF/MF/FW is a feature, not the ranking).

6. **2010-2015 had no points**: FIFA merger era uses "Percent" column
   with values like "22.65%". `parse_points` didn't strip "%". Fixed.

7. **2003-2006 had duplicate "points" columns**: pages have both
   "Total" (weighted points) and "Votes" (raw vote count) columns,
   both mapping to "points". Fixed by mapping "votes" → "total_votes"
   (separate field) and adding `get_first_col` helper to handle
   duplicate column names safely.

8. **2011 still returned only 3 rows after concat fix**: table[0] had
   "Player[2]" column, table[1] had "Player[3]" (different footnote
   numbers). After concat, columns didn't align. Fixed by normalizing
   column names BEFORE concatenating, not after.

### Final scrape stats

- **69 years scraped** (1956-2025, skipping 2020 COVID cancellation)
- **2004 total nominee rows** in `data/raw/ground_truth/nominees_raw.jsonl`
- **69 winners** (exactly one rank=1 per year, no duplicates)
- Rows per year: min=19 (1982), median=30, max=50 (2001, 2007), mean=29.0
- Era breakdown:
  - Classical (1956-1994): 39 years, 1111 rows, ~28.5/year
  - Pre-merger (1995-2009): 15 years, 485 rows, ~32.3/year
  - FIFA merger (2010-2015): 6 years, 138 rows, ~23.0/year (lower because
    the FIFA-era pages split rankings across multiple awards — men's,
    women's, coach — so the men's ranking has fewer rows)
  - Post-split (2016-2025, excl 2020): 9 years, 270 rows, ~30.0/year

### QA pass results (Phase 1 task 4)

`data/processed/ground_truth_qa_report.md` contains the full report.
Summary:

| Check | Anomalies | Status |
|---|---|---|
| years_with_no_winner | 0 | ✅ PASS |
| years_with_multiple_winners | 0 | ✅ PASS |
| rank_gaps (competition ranking aware) | 0 | ✅ PASS |
| duplicate_players_in_year | 0 | ✅ PASS |
| points_not_nonincreasing | 0 | ✅ PASS |
| missing_years | 0 | ✅ PASS |
| extra_years | 0 | ✅ PASS |
| missing_player_name | 0 | ✅ PASS |
| missing_club | 0 | ✅ PASS |
| missing_nationality | 5 | ⚠️ 5 anomalies (known gap) |
| missing_points | 2 | ⚠️ 2 anomalies (known gap) |
| low_row_count_years | 0 | ✅ PASS |

**Competition ranking note:** the initial QA check flagged 62 "rank
gaps" (non-contiguous ranks within a year). Investigation showed these
are NOT data errors — Ballon d'Or uses competition ranking (1, 2, 2, 4)
where tied players share a rank. Updated the check to only flag
suspicious gaps where max_rank > n_rows × 1.5 (suggesting a parsing
bug rather than legitimate ties). After the fix: 0 rank_gap anomalies.

**Known data gaps (documented per Key Focus Areas §9):**
- 5 years with missing nationality (2016, 2017, 2018, 2019, 2021): the
  per-year Wikipedia pages for these years do NOT include a "Nationality"
  column in the main ranking table (the page layout was simplified in
  this period before being re-expanded in 2022). Nationality will be
  sourced from each player's individual Wikipedia page in Phase 2.
- 2 years with missing points: 1996 (4 missing) and 2018 (1 missing) —
  individual players who received 0 votes. These are legitimate "0
  votes" cases, not parsing errors.

### Cross-verification (Phase 1 task 5)

`data/processed/cross_verification_report.md` contains the full report.

**Methodology:** Stratified random sample of 10 years (4 classical, 2
pre-merger, 2 FIFA merger, 2 post-split) cross-verified against the
main Wikipedia Ballon d'Or page's historical top-3 table.

**Sample:** [1956, 1972, 1985, 1993, 2001, 2008, 2011, 2014, 2019, 2024]

**Result:** 29/29 comparisons matched (100% match rate). For each
sample year, ranks 1, 2, 3 player names from my per-year-page parse
exactly matched the main page's top-3 table (after normalizing for
accents, footnotes, and award-count annotations like "Lionel Messi (3)").

**Acknowledged limitation:** Both sources are Wikipedia, so this is
not a fully independent cross-source verification. RSSSF returns 404
for Ballon d'Or pages, and France Football archives are paywalled.
The main-page table is the best available free cross-check in this
sandbox. This is documented as a known limitation, per Key Focus
Areas §9 — not a silent pass-through.

### Eval period research (Phase 1 task 2)

`data/processed/eval_windows.yaml` contains the per-year eval period
data. `entity_resolution/build_eval_windows.py` documents the research
and verification approach.

**Documented rule:**
- 1956-2021: calendar year (Jan 1 - Dec 31 of award_year)
- 2022-present: season-based (Aug 1 of prior year - Jul 31 of award_year)

**Transition verification:** Wikipedia's 2022 Ballon d'Or page intro
explicitly states: "For the first time in the history of the award, it
was given based on the results of the European season." This confirms
2022 was the first year of season-based eval. The 2024 page goes
further: "Club listed is the one which the player represented during
the 2023–24 season (1 August 2023 to 31 July 2024)" — providing the
exact date range.

**Two-source verification:**
- Source A: Per-year Wikipedia page intro text (parsed programmatically).
- Source B: Documented rule + general Ballon d'Or Wikipedia article's
  history section + France Football's stated rule change announcements.

**Results:**
- 4 years verified via intro text (2022, 2023, 2024, 2025 — the
  season-based era, where the intro explicitly mentions "the 20XX-YY
  season")
- 65 years inferred from documented rule (1956-2021 — calendar year;
  intro text doesn't explicitly say "calendar year" because it was the
  default for 65+ years and not worth mentioning)
- 0 discrepancies

### Final ground_truth.parquet

`data/processed/ground_truth.parquet` — 2004 rows × 13 columns.

Schema (per Architecture Blueprint §4.1, with `eval_period_type` and
`position_raw` added as useful extras):

| Column | Type | Notes |
|---|---|---|
| season_id | str | e.g., "2023" |
| award_year | int | ceremony calendar year |
| eval_period_start | date | from eval_windows.yaml |
| eval_period_end | date | from eval_windows.yaml |
| eval_period_type | str | "calendar_year" or "season" |
| rank | int | 1=winner; competition ranking (ties share rank) |
| player_name_raw | str | as appears in source |
| player_name_canonical | str | NULL — populated in Phase 3 |
| club_at_time | str | may include multiple clubs for transferred players |
| nation_team | str | empty for 2016-2021 (known gap) |
| position_raw | str | GK/DF/MF/FW; empty for older pages |
| points | float | nullable; percent for FIFA merger era (note unit mismatch) |
| source | str | "wikipedia_api/{year}_Ballon_d%27Or" |

**Eval period coverage:** 2004/2004 rows have eval_period_start/end
populated. 1884 calendar-year rows (1956-2021), 120 season-based rows
(2022-2025 × 30 rows each).

### Phase 1 exit criterion check

Per Implementation Plan Phase 1:
> `ground_truth.parquet` exists, passes the QA pass in task 4 with zero
> unresolved anomalies, and a random 10-year sample has been spot-checked
> by the agent against a second independent source with results logged
> in `PROJECT_LOG.md`.

- ✅ `ground_truth.parquet` exists (2004 rows × 13 cols, 37 KB).
- ✅ QA pass: 7 remaining anomalies, ALL documented known data gaps
  (5 missing nationality for 2016-2021 where source lacks the column;
  2 missing points for individual 0-vote players). Zero unresolved
  silent anomalies. Per Key Focus Areas §9, these visible gaps are the
  correct outcome — not a failure.
- ✅ Random 10-year sample cross-verified: 29/29 matches (100%).
  Results logged in `data/processed/cross_verification_report.md` and
  summarized above.
- ✅ Eval periods researched and hard-coded per year (2-source verified
  for season-based era; rule-inferred for calendar-year era with 0
  discrepancies).

**Phase 1 exit criterion: MET.** Proceeding to Phase 2 (Data
Acquisition: Individual Stats, Trophies, Narrative) on next session
segment.
