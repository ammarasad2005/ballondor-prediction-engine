# Ballon d'Or Prediction Engine — Requirements

## Part A: Data Requirements

### A.1 Ground Truth Data (Phase 1 dependency)

| Data | Granularity | Era coverage needed | Notes |
|---|---|---|---|
| Ballon d'Or winner + top-5 (or fuller, where available) nominee list | per year | 1956–present | Backbone table; see Architecture Blueprint §4.1 |
| Evaluation period per year (calendar-year vs season-based) | per year | 1956–present | Must be individually verified, not assumed uniform |
| FIFA World Player of the Year results, 1991–2009 | per year | historical cross-reference | Useful cross-check for the 2010-2015 merger period and for understanding voting-criteria evolution |
| Ballon d'Or Féminin results | per year | 2018–present | **Out of scope by default** per user's confirmed CLI/men's-award focus — only include if separately requested |

### A.2 Individual Performance Data (Phase 2 dependency)

**Modern era (2014–15 onward) — full feature depth target:**
- Goals, assists, minutes played, appearances (all competitions + league-only splits)
- xG, xA, and over/underperformance vs xG
- Progressive passes/carries, shot-creating actions (where sourceable)
- Per-90 normalized versions of all of the above
- Position designation (needed for position-adjustment features)

**Classical era (1956–2014) — simplified target per P4:**
- Goals, assists (assists notoriously poorly recorded pre-1990s — expect
  and document gaps rather than forcing values)
- Appearances, minutes (minutes often unavailable pre-1990s — appearances
  may be the only reliable durability proxy for older seasons)
- Position designation

### A.3 Team & Trophy Data (Phase 2 dependency)

- Domestic league title winner, by country and year
- Continental club competition winner (European Cup / Champions League;
  also relevant secondary competitions where applicable) by year, plus
  finalist/semifinalist status for the "deep run" feature
- International tournament results by calendar year (World Cup, continental
  championships e.g. Euro/Copa América) — winner, and player's individual
  tournament performance (goals, Golden Ball/Boot-type individual honors)
  where available
- Domestic league final table position, by team and season

### A.4 Narrative/Media Signal Data (Phase 2 dependency, best-effort)

- Any structured "signature moment" indicators the agent can source
  (e.g., a hat-trick in a major final, a decisive individual tournament
  performance) — where not structurally available, this may require
  agent judgment calls; **any agent-inferred (rather than sourced) flag
  must be logged as such**, per Implementation Plan Phase 2 task 4
- Club prestige / market-size proxy — a simple, defensible operationalization
  (e.g., club revenue tier, European competition coefficient at the time)
  is preferable to a subjective agent judgment call; document whichever
  proxy is chosen and why

### A.5 Data Sourcing Approach

Per the user's confirmed preference, the agent has **full autonomy** to
source all of the above via web search and scraping — no seed files will
be provided. Consequences the agent should plan for:
- Source availability, structure, and reliability will vary significantly
  by era — expect to use different sources/scrapers for classical vs
  modern era data, as noted throughout the Architecture Blueprint
- The agent must verify the terms of use / robots.txt posture of any
  site before scraping at volume, and should prefer sources structured
  for programmatic access (structured tables, stable page templates)
  over sources that require heavy per-page bespoke parsing
- Where a plausible source is paywalled, rate-limited, or otherwise
  inaccessible, the agent should seek an alternative source rather than
  fabricate or estimate values — fabricated data is a strictly worse
  outcome than a documented gap
- The network configuration available to the agent's sandbox should be
  checked at build time — if the sandbox's allowed domains are restrictive,
  the agent should surface this immediately rather than silently produce
  an incomplete dataset

## Part B: Technical / Software Requirements

### B.1 Core Language & Environment

- Python 3.10+ as primary language (data pipeline, modeling, scraping)
- A reproducible dependency spec (`requirements.txt` or `pyproject.toml`)
  committed at repo root, generated as dependencies are actually used —
  do not pre-guess an exhaustive list; add as needed and keep it current

### B.2 Scraping & Web Access

- `requests` / `httpx` for straightforward HTTP fetches
- `beautifulsoup4` (`bs4`) for HTML parsing of structured tables (well
  suited to Wikipedia-style tables)
- `pandas.read_html` as a fast path for simple structured tables where
  applicable
- A headless browser tool (`playwright` or `selenium` +
  `undetected_chromedriver`) held in reserve for any source that requires
  JS rendering or has bot-detection — only introduce this complexity if
  the simple-fetch path fails for a given source, per the project's own
  established precedent (this pattern was used previously for an
  Akamai-WAF-blocked source)
- Respect for rate limits — all scrapers should include deliberate
  request pacing/backoff, not just a bare fetch loop, given the amount
  of historical data being pulled across ~69 years

### B.3 Data Handling

- `pandas` for tabular manipulation throughout
- `pyarrow` for Parquet read/write (used as the canonical on-disk format
  per Architecture Blueprint §5)
- `pyyaml` for config and feature-registry files
- `rapidfuzz` (preferred over `fuzzywuzzy` for license/performance
  reasons) for entity-resolution fuzzy matching

### B.4 Modeling

- `scikit-learn` for the Tier B pairwise linear ranker (can be
  implemented directly as pairwise logistic regression, or via
  `sklearn`'s standard classifier on constructed pairs)
- `xgboost` or `lightgbm` for the Tier C ranker — either is acceptable;
  `xgboost`'s `rank:pairwise`/`rank:ndcg` objectives are a reasonable
  default choice given wide documentation and community precedent for
  learning-to-rank tasks
- `shap` for GBM explanation contributions (Architecture Blueprint §4.7)
- `scipy.stats` for Spearman/Kendall rank correlation metrics

### B.5 Validation & Experiment Tracking

- No heavyweight experiment-tracking platform is required given project
  scale — plain versioned JSON/Markdown reports under `reports/` is
  sufficient and keeps the pipeline dependency-light
- `matplotlib` (or similar) for any diagnostic plots the agent produces
  during Phase 4 sanity checks and Phase 6 validation reporting —
  plots are for the agent's/user's inspection during development, not
  a required deliverable in themselves

### B.6 VLM Usage

The agent has VLM capability available and should use it specifically
for:
- Disambiguating entity-resolution edge cases where a photo or visual
  reference helps confirm identity (Implementation Plan Phase 3 task 3)
- Inspecting any scraped table screenshots or malformed-render diagnostics
  if a scraper's HTML parsing is behaving unexpectedly and a visual check
  of the live page would clarify structure
- Any sanity-check visualization review during Phase 4/6 where reading a
  generated chart is faster/more reliable than parsing raw numbers

### B.7 Explicitly Out of Scope for This Build

- Web application framework / frontend libraries (deferred to Phase 8
  handoff only — no implementation now)
- Any paid/licensed data API — the project should run entirely on
  freely and legally accessible sources
- Real-time/live data ingestion — the system operates on completed
  historical seasons plus a periodically-refreshed current-season
  candidate pool, not live match-by-match updates

## Part C: Compute Requirements

Given N≈300-350 candidate-seasons and the model tiers specified, this
project has modest compute needs — no GPU requirement is anticipated.
Standard CPU-based sandboxed execution is sufficient for all scraping,
feature engineering, and model training (including the GBM tier, which
at this data scale trains in seconds to low minutes).
