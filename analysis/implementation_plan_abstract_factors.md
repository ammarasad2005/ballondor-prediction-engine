# Implementation Plan — Abstract Jury Factors (Iteration 2)

**Date:** 2026-07-29
**Status:** Ready for implementation
**Prerequisite:** Audit fixes (Finding 3, 8, 10) — COMPLETE

---

## Overview

Per the self-improving backtracking process outlined in `analysis/abstract_factors_framework.md`, this plan implements the 7 abstract jury factors identified through reverse-engineering of missed winners. Each factor is prioritized by **impact** (how many missed winners it explains) and **feasibility** (data sourceability).

**Critical rule:** Each factor is validated on LOSO CV only. The held-out set (2018-2025) remains untouched until all factors are implemented and we're confident in the model. Per Key Focus Areas §8, held-out is a one-shot final check.

---

## Priority Order

| Priority | Factor | Impact | Feasibility | Est. Effort |
|---|---|---|---|---|
| **P0** | C. Geographic representation | 33% of missed winners | High (computed from ground truth) | 2 hours |
| **P0** | B. Tactical engine role (position-adjusted) | 17% | High (derivable from existing data) | 2 hours |
| **P0** | A. Tournament heroism (knockout goals) | 17% | Medium (Understat per-match data cached) | 4 hours |
| **P1** | D. Career narrative arc | ~15% | High (computed from ground truth) | 3 hours |
| **P1** | E. Defensive dominance (captain flag) | 17% | High (Wikipedia infobox) | 3 hours |
| **P2** | G. Voter fatigue | ~8% | High (computed from ground truth) | 1 hour |
| **P2** | F. Aesthetic brilliance | ~8% | Low (dribble data partial) | 4 hours |

**Total estimated effort:** ~19 hours of implementation

---

## P0-A: Tournament Heroism (Knockout Performance)

### What it captures
Performance in the biggest matches — UCL knockout rounds (R16 through final), WC knockout rounds, Euro/Copa knockout rounds. The jury weights "he won us the World Cup" far more than "he scored 30 league goals."

### Why it matters
17% of missed winners (Modrić 2018, Cannavaro 2006, Rodri 2024) won primarily on tournament heroism. The current model only has `ucl_winner` (boolean) — it doesn't know if the player actually performed in the knockout stages or just rode the bench.

### Data source
**Understat per-match data** — already cached at `data/raw/understat/pages/`. Each player's `getPlayerData/{id}` response includes a `matches` array with per-match stats. We need to:
1. Identify which matches are UCL knockout rounds (match date + competition context)
2. Sum goals + assists in those matches
3. Also fetch WC knockout data from Wikipedia WC pages

### Implementation steps
1. **Extend Understat scraper** to also fetch per-match data via `getPlayerData/{player_id}` endpoint
2. **Build knockout match identifier** — for UCL: matches after February in the `matches` array with `league` field containing "Champions League" or similar
3. **Compute `ucl_knockout_goals`** — sum of goals in UCL R16/QF/SF/Final matches
4. **Compute `ucl_knockout_assists`** — same for assists
5. **For WC/Euro/Copa** — scrape Wikipedia tournament pages for player-level knockout performance (goals in knockout rounds)
6. **Compute `wc_knockout_goals`** — goals in WC R16/QF/SF/Final
7. **Compute `final_match_goals`** — goals in any major final (UCL final, WC final, Euro final, Copa final)

### Features added
```
ucl_knockout_goals        (int, modern_only — Understat covers 2014+)
ucl_knockout_assists      (int, modern_only)
wc_knockout_goals         (int, all_eras — from Wikipedia)
final_match_goals         (int, all_eras — from Wikipedia)
tournament_heroism_score  (float, derived: weighted sum of above)
```

### Expected impact
Would catch Modrić 2018 (WC final + Golden Ball), Cannavaro 2006 (WC defensive masterclass), Rodri 2024 (Euro final goal). Also helps with Benzema 2022 (UCL knockout heroics) and Messi 2023 (WC knockout performance).

### Success criteria
- LOSO CV top-1 improves by ≥2pp
- At least 2 of the 3 "tournament heroism" missed winners (Modrić, Cannavaro, Rodri) move into top-5 predicted rank

---

## P0-B: Tactical Engine Role (Position-Adjusted Value)

### What it captures
Midfielders and defenders whose value is in ball progression, not goalscoring. The "engine" of the team — players like Rodri, Modrić, Busquets, Makelele.

### Why it matters
17% of missed winners (Modrić 2018, Rodri 2024) are midfielders whose stats look modest but whose tactical value is immense. The current model treats all positions the same — a midfielder with 5 goals gets the same "goals signal" as a striker with 5 goals, even though the midfielder's 5 goals are much more notable.

### Data source
**Existing data** — `position_raw` column (partially populated) + `xg`/`xa` from Understat. No new scraping needed.

### Implementation steps
1. **Populate `position` for all eras** — parse Wikipedia player infoboxes for the "Position(s)" field. The cached player pages at `data/raw/stats/pages_api/` have this in the infobox HTML.
2. **Normalize positions** to 4 categories: GK, DF, MF, FW
3. **Compute `position_adjusted_xg_contribution`** — `(xG + xA) × position_multiplier` where:
   - FW: 1.0 (baseline)
   - MF: 1.5 (midfielder stats are 50% more notable)
   - DF: 2.0 (defender stats are 2× more notable)
   - GK: 3.0 (GK stats are 3× more notable, though GKs rarely have xG/xA)
4. **Compute `position_rarity_score`** — how rare is a winner at this position? Based on historical winner distribution:
   - GK: 1/69 winners = 1.4% → rarity_score = 3.0
   - DF: ~5/69 = 7.2% → rarity_score = 2.0
   - MF: ~15/69 = 21.7% → rarity_score = 1.0
   - FW: ~48/69 = 69.6% → rarity_score = 0.5

### Features added
```
position                  (str: GK/DF/MF/FW, all_eras)
position_adjusted_xg_contribution  (float, modern_only — needs xG)
position_rarity_score     (float, all_eras — computed from ground truth)
```

### Expected impact
Would catch Rodri 2024 (MF with position multiplier), Modrić 2018 (MF), potentially future "engine" winners. Also helps the model understand why Yashin 1963 (GK) and Cannavaro 2006 (DF) won despite low goals.

### Success criteria
- LOSO CV top-1 improves by ≥1pp
- Rodri 2024 and Modrić 2018 both move into top-10 predicted rank

---

## P0-C: Geographic Representation

### What it captures
The jury gives recognition to players from underrepresented regions/nations. This is a documented voting pattern — George Weah (only African winner), Blokhin/Belanov (Soviet), Albert (Hungarian), Yashin (Soviet GK).

### Why it matters
**33% of missed winners** — the single biggest factor. The jury implicitly considers "representational" value — recognizing football from regions that rarely produce Ballon d'Or winners.

### Data source
**Existing data** — `nation_team` column in ground truth (already populated). No new scraping needed. All features computed from the ground truth itself.

### Implementation steps
1. **Compute `nation_prior_winners_count`** — for each (player, year), count how many prior Ballon d'Or winners came from this player's nation (strictly before this year)
2. **Compute `continent_prior_winners_count`** — same but at continent level
3. **Compute `nation_years_since_last_winner`** — years since a player from this nation last won (999 if never)
4. **Map nations to continents** — manual lookup table (Europe, South America, Africa, Asia, North America, Oceania)
5. **Compute `league_visibility_tier`** — tier of the player's domestic league:
   - Tier 1: EPL, La Liga, Serie A, Bundesliga, Ligue 1
   - Tier 2: Eredivisie, Portuguese Liga, Russian Premier League, Turkish Super Lig
   - Tier 3: Other European leagues
   - Tier 4: Non-European leagues

### Features added
```
nation_prior_winners_count        (int, all_eras)
continent_prior_winners_count     (int, all_eras)
nation_years_since_last_winner    (int, all_eras — 999 if never)
league_visibility_tier            (int 1-4, all_eras)
```

### Expected impact
Would catch Weah 1995 (first African), Blokhin 1975 (Soviet recognition), Albert 1967 (Hungarian), Belanov 1986 (Soviet). These are all "first/regional recognition" winners.

### Success criteria
- LOSO CV top-1 improves by ≥3pp (this is the highest-impact factor)
- At least 2 of the 4 "geographic representation" missed winners move into top-5

---

## P1-D: Career Narrative Arc

### What it captures
The jury is influenced by storylines — redemption arcs, breakout seasons, farewell tours, "overdue" recognition.

### Why it matters
~15% of missed winners have a strong narrative component (Messi 2021 Copa América redemption, Dembélé 2025 breakout).

### Data source
**Existing data** — computed from ground truth + career stats already in features.parquet. No new scraping needed.

### Implementation steps
1. **Compute `years_in_top_5`** — how many prior years the player finished top-5 in Ballon d'Or ("overdue" factor)
2. **Compute `first_time_nominee_flag`** — is this the player's first Ballon d'Or nomination?
3. **Compute `career_goals_vs_prior_avg`** — this season's goals vs player's career average (breakout detection)
4. **Compute `age_career_stage`** — age × career trajectory:
   - Young breakout (age < 23): high novelty
   - Peak (age 23-29): expected dominance
   - Veteran farewell (age 32+): sentimental factor
5. **Compute `previous_finals_losses`** — number of major finals the player lost previously (redemption arc detection). Requires manual curation for top players.

### Features added
```
years_in_top_5              (int, all_eras)
first_time_nominee_flag     (bool, all_eras)
career_goals_vs_prior_avg   (float, all_eras — needs career stats)
age_career_stage            (str: young/peak/veteran, all_eras)
previous_finals_losses      (int, best-effort — manual curation)
```

### Success criteria
- LOSO CV top-1 improves by ≥1pp
- Messi 2021 moves into top-5 (Copa América redemption arc)

---

## P1-E: Defensive Dominance (Captain Flag + Position Rarity)

### What it captures
For defenders and goalkeepers, the jury sometimes recognizes defensive mastery — clean sheets, goals prevented, organizational leadership.

### Why it matters
17% of missed winners (Yashin 1963, Cannavaro 2006, Beckenbauer 1972/1976, Sammer 1996) won on defensive dominance.

### Data source
**Wikipedia infoboxes** — `captain` field is in the player infobox. Also `position_raw` (partially populated).

### Implementation steps
1. **Parse `captain_flag`** from Wikipedia player infoboxes — look for "Captain" or "Captain of" in the infobox
2. **Compute `defensive_position_flag`** — True if position is GK or DF
3. **Compute `wc_won_as_captain`** — True if player captained their nation to WC victory (combines `captain_flag` + `world_cup_winner`)
4. **Populate `position` for all eras** — parse Wikipedia infobox "Position(s)" field for all 835 players (currently only 6% populated)
5. **Compute `position_rarity_score`** — (already covered in P0-B, but listed here for completeness)

### Features added
```
captain_flag            (bool, all_eras)
defensive_position_flag (bool, all_eras)
wc_won_as_captain       (bool, all_eras — interaction of captain + WC winner)
```

### Success criteria
- LOSO CV top-1 improves by ≥1pp
- Cannavaro 2006 (WC-winning captain) moves into top-10

---

## P2-G: Voter Fatigue ("Someone New" Factor)

### What it captures
After Messi/Ronaldo won everything for a decade, voters may implicitly want to recognize someone new.

### Why it matters
~8% of missed winners (Modrić 2018 was partly a "voter fatigue" vote).

### Data source
**Existing data** — computed from ground truth. No new scraping.

### Implementation steps
1. **Compute `years_since_new_winner`** — years since a first-time winner won the Ballon d'Or
2. **Compute `consecutive_wins_by_same_player`** — has the same player won the last N years?
3. **Compute `messi_ronaldo_dominance_period`** — flag for years 2008-2017 when Messi/Ronaldo won 10 consecutive years

### Features added
```
years_since_new_winner              (int, all_eras)
consecutive_wins_by_same_player     (int, all_eras)
```

### Success criteria
- LOSO CV top-1 improves by ≥0.5pp
- Modrić 2018 moves into top-5 (voter fatigue signal)

---

## P2-F: Aesthetic Brilliance (Deferred)

### What it captures
Some players are more aesthetically pleasing — dribbling, flair, "beautiful football."

### Why it matters
~8% of missed winners (Ronaldinho 2005).

### Data source
**Understat per-match data** (for dribbles) + potentially YouTube API for highlight views. Low feasibility.

### Implementation steps
1. **Fetch `successful_dribbles`** from Understat per-match data
2. **Compute `fouls_won`** from Understat (already in per-match data)
3. **Compute `dribble_success_rate`** — successful dribbles / total dribble attempts

### Features added
```
successful_dribbles     (int, modern_only)
fouls_won               (int, modern_only)
dribble_success_rate    (float, modern_only)
```

### Success criteria
- Marginal improvement only — this is a "nice to have" factor
- Ronaldinho 2005 does NOT move significantly (insufficient data for 2005 era)

---

## Validation Protocol

After implementing each priority tier:

### Step 1: LOSO CV (mandatory after each tier)
- Run LOSO CV with the expanded feature set
- Compare top-1, top-3, top-5, Spearman metrics vs previous iteration
- **Decision rule:** Keep new features if LOSO CV top-1 OR top-3 improves by ≥1pp. Otherwise backtrack.

### Step 2: Cross-era analysis (after each tier)
- Check if improvement is concentrated in one era (e.g., only modern era improves)
- If classical era degrades significantly, consider era-specific feature weighting

### Step 3: Error analysis (after P0 complete)
- Re-run the reverse-engineering analysis on the updated model
- Check how many of the 16 originally-missed winners are now correctly predicted
- Identify NEW missed winners (if any) that the new features didn't help with

### Step 4: Held-out evaluation (ONLY after all P0+P1 complete)
- Per Key Focus Areas §8, this is a ONE-SHOT check
- If performance is poor, that is a final honest finding — NOT a cue to tune
- Report honestly regardless of outcome

---

## Iteration Schedule

| Iteration | Scope | Validation | Held-out? |
|---|---|---|---|
| **Iter 2a** | P0-C (Geographic) + P0-B (Position-adjusted) | LOSO CV | ❌ NO |
| **Iter 2b** | P0-A (Tournament heroism) | LOSO CV | ❌ NO |
| **Iter 2c** | P1-D (Career narrative) + P1-E (Defensive) | LOSO CV | ❌ NO |
| **Iter 2d** | P2-G (Voter fatigue) | LOSO CV | ❌ NO |
| **Iter 2e** | Full error analysis + held-out evaluation | Full validation | ✅ ONE-SHOT |

**After Iter 2e:** If held-out shows improvement, the model is ready for production use. If not, the honest finding is that the abstract factors don't generalize — and we need to accept the current accuracy ceiling or find completely new data sources.

---

## Feature Registry Updates

After implementation, update `features/feature_registry.yaml` with:

```yaml
# NEW: Family 8 — Abstract jury factors
abstract_factors:
  # P0-A: Tournament heroism
  ucl_knockout_goals:
    era: modern_only
    source: understat_per_match
  wc_knockout_goals:
    era: all_eras
    source: wikipedia
  final_match_goals:
    era: all_eras
    source: wikipedia
  
  # P0-B: Tactical engine role
  position:
    era: all_eras
    source: wikipedia_infobox
  position_adjusted_xg_contribution:
    era: modern_only
    derived_from: [xg, xa, position]
  position_rarity_score:
    era: all_eras
    derived_from: [position, ground_truth_winners]
  
  # P0-C: Geographic representation
  nation_prior_winners_count:
    era: all_eras
    derived_from: [ground_truth]
  continent_prior_winners_count:
    era: all_eras
    derived_from: [ground_truth]
  league_visibility_tier:
    era: all_eras
    source: manual_lookup
  
  # P1-D: Career narrative
  years_in_top_5:
    era: all_eras
    derived_from: [ground_truth]
  first_time_nominee_flag:
    era: all_eras
    derived_from: [ground_truth]
  
  # P1-E: Defensive dominance
  captain_flag:
    era: all_eras
    source: wikipedia_infobox
  wc_won_as_captain:
    era: all_eras
    derived_from: [captain_flag, world_cup_winner]
  
  # P2-G: Voter fatigue
  years_since_new_winner:
    era: all_eras
    derived_from: [ground_truth]
```

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| New features cause overfitting (like Tier C after audit fixes) | Medium | High | Use L1 regularization; monitor LOSO CV closely |
| Geographic representation factor is era-specific (mattered in classical, not modern) | High | Medium | Add era interaction term; let model learn era-specific weights |
| Position data can't be populated for classical era | Medium | Medium | Use `has_stats_data` flag as proxy; document the gap |
| Understat per-match API is rate-limited | Low | Low | Use cached data; 835 players × 1.5s delay = ~21 min |
| Held-out evaluation shows no improvement | Medium | Critical | Accept honestly; document as "the abstract factors don't generalize to modern era" |

---

## Definition of Done

The iteration is complete when:
1. All P0 factors (A, B, C) are implemented and validated on LOSO CV
2. At least 2 of the 3 P1 factors (D, E) are implemented
3. P2 factors (F, G) are implemented if time permits
4. A full error analysis shows how many of the 16 originally-missed winners are now caught
5. Held-out evaluation has been run ONE time, honestly reported
6. `features/feature_registry.yaml` updated with all new features
7. `PROJECT_LOG.md` documents every decision, success, and failure
