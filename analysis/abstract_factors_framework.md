# Abstract Jury Factors → Quantifiable Metrics Framework

**Date:** 2026-07-29
**Purpose:** Systematically identify the abstract factors that affect Ballon d'Or jury decisions, and map each to quantifiable proxies. This is the intellectual core of the self-improving backtracking process.

**Key insight from reverse-engineering analysis:** The jury's "pure judgement" is not random — it follows identifiable patterns. Of 16 winners our model missed, the top recurring abstract factors are:
1. Geographic representation (33%) — recognition of underrepresented regions
2. Defensive dominance (17%) — defenders/GKs getting positional recognition
3. Tournament heroism (17%) — clutch performance in big matches
4. Tactical engine role (17%) — midfielders whose value isn't in goals

---

## Framework: Three Tiers of Abstract Factors

### Tier 1: Factors Already Captured (but possibly poorly)

These are in the current feature set. The audit showed gaps in implementation.

| Factor | Current Feature | Gap |
|---|---|---|
| Individual statistical production | total_goals, xg, xa | ✅ captured (but assists are 100% NaN — Finding 3 bug) |
| Team trophy success | ucl_winner, domestic_league_winner | ✅ captured |
| International tournament success | world_cup_winner, euro_winner | ✅ captured |
| Club prestige/market size | club_prestige_tier | ✅ captured (manual lookup) |
| Previous winner reputation | previous_ballon_dor_winner | ✅ captured (strictly lagged) |
| Peer-relative standing | goals_percentile_in_year | ✅ captured (selection bias noted) |

### Tier 2: Identified but Not Quantified

These were mentioned in the spec or audit but never implemented.

| Factor | Why It Matters | Quantification Approach |
|---|---|---|
| **Position bias** | Attackers win 80%+ of the time; defenders/GKs need exceptional circumstances | Add `position` feature (GK/DF/MF/FW); populate for all eras via Wikipedia infobox |
| **Recency bias** | Voters weight Sept-Nov matches heavier than Jan-March | Split stats by half-season; source from Understat per-match data |
| **Signature moment** | Iconic individual performance (hat-trick in final, WC heroics) | Redesign as non-collinear: "UCL_knockout_goals + WC_knockout_goals" instead of boolean flag |

### Tier 3: The Real Gap — Abstract Factors Not Yet Identified

This is where the self-improving process needs to focus. Based on the reverse-engineering analysis of 16 missed winners:

---

## Factor A: Tournament Heroism (17% of missed winners)

**Definition:** Performing in the biggest matches — WC knockout rounds, UCL knockout rounds, finals specifically.

**Why the jury weights this:** Voters remember "he won us the World Cup" not "he scored 30 goals in the league." Big-match performance is disproportionately salient.

**Quantifiable proxies:**

| Metric | Definition | Data Source | Feasibility |
|---|---|---|---|
| `ucl_knockout_goals` | Goals in UCL Round of 16 through final | Understat (per-match data available) | High — already have per-match JSON |
| `ucl_knockout_assists` | Assists in UCL knockout rounds | Understat | High |
| `wc_knockout_goals` | Goals in WC knockout rounds (Round of 16+) | Wikipedia WC pages | Medium — needs new scraper |
| `wc_knockout_assists` | Assists in WC knockout rounds | Wikipedia | Medium |
| `final_match_goals` | Goals in any final (UCL, WC, Euro, Copa, domestic cups) | Multiple sources | Medium |
| `final_match_motm` | Man of the Match in a final | Wikipedia | Low — subjective, hard to source |

**Expected impact:** Would catch Modrić 2018 (WC final + Golden Ball), Cannavaro 2006 (WC defensive masterclass), Rodri 2024 (Euro final goal).

---

## Factor B: Tactical Engine Role (17% of missed winners)

**Definition:** Midfielders/defenders whose value is in ball progression, not goalscoring. The "engine" of the team.

**Why the jury weights this:** Some voters (especially coaches/captains in the modern voting system) recognize positional value beyond raw stats. Rodri, Modrić, Makelele, Busquets — these players don't score much but are tactically indispensable.

**Quantifiable proxies:**

| Metric | Definition | Data Source | Feasibility |
|---|---|---|---|
| `progressive_passes` | Passes that move the ball significantly toward goal | fbref (blocked) / StatsBomb (event data) | Low — fbref blocked, StatsBomb heavy |
| `passes_into_final_third` | Passes into the attacking third | fbref / StatsBomb | Low |
| `passes_into_penalty_area` | Passes into the box | fbref / StatsBomb | Low |
| `tackles_won` | Successful tackles | Understat (partial) / fbref | Medium |
| `interceptions` | Defensive interceptions | fbref / StatsBomb | Low |
| `ball_recoveries` | Times player regained possession | fbref / StatsBomb | Low |
| `position_adjusted_xg_contribution` | xG + xA but adjusted for position (MF/DF get a multiplier) | Derived from existing xG/xA | High — can compute now |

**Expected impact:** Would catch Rodri 2024, Modrić 2018, and potentially future "engine" winners.

**Practical approach:** Since fbref is blocked, use `position_adjusted_xg_contribution` as a proxy. A midfielder with 5 goals + 10 assists is more notable than a striker with the same stats. Compute as: `(xG + xA) × position_multiplier` where position_multiplier = 1.0 for FW, 1.5 for MF, 2.0 for DF, 3.0 for GK.

---

## Factor C: Geographic Representation (33% of missed winners — the biggest gap)

**Definition:** The jury gives recognition to players from underrepresented regions/nations. This is a documented voting pattern — George Weah (only African winner), Blokhin/Belanov (Soviet recognition), Albert (Hungarian), Yashin (Soviet GK).

**Why the jury weights this:** Voters are geographically diverse (especially after 2007 when voting went global). There's an implicit "representational" factor — recognizing football from regions that rarely produce Ballon d'Or winners.

**Quantifiable proxies:**

| Metric | Definition | Data Source | Feasibility |
|---|---|---|---|
| `nation_prior_winners_count` | Number of prior Ballon d'Or winners from this player's nation | Computed from ground truth | High — can compute now |
| `continent_prior_winers_count` | Number of prior winners from this player's continent | Computed | High |
| `nation_years_since_last_winner` | Years since a player from this nation last won | Computed | High |
| `league_visibility_tier` | Tier of the player's domestic league (1=EPL/La Liga, 2=Serie A/Bundesliga, 3=Ligue 1/Eredivisie, 4=other) | Manual lookup | High |
| `voter_geography_overlap` | Overlap between player's nationality and voter nationalities (post-2007 voting breakdown) | Wikipedia voting pages | Medium |

**Expected impact:** Would catch Weah 1995, Blokhin 1975, Albert 1967, Belanov 1986. These are all "first/regional recognition" winners.

**Caveat:** This factor is most relevant for classical era (pre-1995, Europe-only eligibility had different dynamics). In modern era, the voter pool is global, so geographic representation may matter less.

---

## Factor D: Career Narrative Arc (recurring across multiple missed winners)

**Definition:** The jury is influenced by storylines — redemption arcs, breakout seasons, farewell tours, "overdue" recognition.

**Why the jury weights this:** Voters are human; narratives make players memorable. "Messi finally winning the World Cup" is a more compelling story than "Messi had a good season."

**Quantifiable proxies:**

| Metric | Definition | Data Source | Feasibility |
|---|---|---|---|
| `years_without_international_trophy` | Years since player's nation last won a major international trophy | Computed | High |
| `previous_finals_losses` | Number of major finals the player lost previously | Manual/Wikipedia | Medium |
| `career_goals_vs_prior_avg` | This season's goals vs player's career average (breakout detection) | Computed from career stats | High |
| `age_x_career_stage` | Age × career trajectory (young breakout vs veteran farewell) | Computed | High |
| `years_in_top_5` | How many prior years the player finished top-5 ("overdue" factor) | Computed from ground truth | High |
| `first_time_nominee_flag` | Is this the player's first Ballon d'Or nomination? | Computed | High |
| `transfer_fee_relative` | Transfer fee paid for the player (proxy for perceived talent) | Transfermarkt (blocked) | Low |

**Expected impact:** Would catch Messi 2021 (Copa América redemption), Dembélé 2025 (breakout), and the "overdue" pattern.

---

## Factor E: Defensive Dominance (17% of missed winners)

**Definition:** For defenders and goalkeepers, the jury sometimes recognizes defensive mastery — clean sheets, goals prevented, organizational leadership.

**Why the jury weights this:** Pure attacking stats undervalue defenders. Yashin, Cannavaro, Beckenbauer, Sammer — these winners were recognized for defensive excellence in a tournament year.

**Quantifiable proxies:**

| Metric | Definition | Data Source | Feasibility |
|---|---|---|---|
| `clean_sheets` | Number of clean sheets (team-level, attributed to GK/DF) | Wikipedia team pages | Medium |
| `goals_conceded_per_game` | Team's defensive record when player is on pitch | fbref / Understat | Medium |
| `gk_xg_prevented` | For GKs: goals prevented vs expected (GK-specific xG) | fbref (blocked) / specialized GK sites | Low |
| `defensive_actions` | Tackles + interceptions + blocks + clearances | fbref / StatsBomb | Low |
| `captain_flag` | Is the player the team captain? | Wikipedia infobox | High |
| `position_rarity_score` | How rare is a winner at this position? (GK=very rare, DF=rare, MF=uncommon, FW=common) | Computed from ground truth | High |

**Expected impact:** Would catch Yashin 1963, Cannavaro 2006, Beckenbauer 1972/1976, Sammer 1996.

---

## Factor F: Aesthetic Brilliance / "Highlight Reel" Factor

**Definition:** Some players are more aesthetically pleasing — dribbling, flair, "beautiful football." Ronaldinho, Neymar, Vinícius.

**Why the jury weights this:** Voters watch games, not just stat sheets. A player who produces memorable moments is more likely to be top-of-mind.

**Quantifiable proxies:**

| Metric | Definition | Data Source | Feasibility |
|---|---|---|---|
| `successful_dribbles` | Completed dribbles per season | Understat (per-match) / fbref | Medium |
| `dribble_success_rate` | % of dribble attempts successful | Understat | Medium |
| `nutmegs_skills` | Count of skill moves (very hard to source) | Manual | Low |
| `highlight_views` | YouTube views of player highlights (popularity proxy) | YouTube API | Low |
| `fouls_won` | Times player was fouled (indicates dangerous play) | Understat | High |
| `shots_from_dribbles` | Shots that came from dribbling past defenders | Understat (per-shot data) | Medium |

**Expected impact:** Would catch Ronaldinho 2005. Marginal for other winners.

---

## Factor G: Voter Fatigue / "Someone New" Factor

**Definition:** After Messi/Ronaldo won everything for a decade, voters may implicitly want to recognize someone new.

**Why the jury weights this:** Documented in 2018 — Modrić's win was partly because voters were "tired" of Messi/Ronaldo winning every year.

**Quantifiable proxies:**

| Metric | Definition | Data Source | Feasibility |
|---|---|---|---|
| `years_since_new_winner` | Years since a first-time winner won | Computed from ground truth | High |
| `consecutive_wins_by_same_player` | Has the same player won the last N years? | Computed | High |
| `voter_turnover` | How many new voters this year vs last? | Wikipedia voting pages | Low |

**Expected impact:** Would catch Modrić 2018, potentially future "passing of the torch" moments.

---

## Prioritization Matrix

| Factor | Impact (missed winners explained) | Feasibility (data sourceable) | Priority |
|---|---|---|---|
| A. Tournament heroism | 17% | High (Understat per-match) | **P0 — implement first** |
| B. Tactical engine role | 17% | Medium (position-adjusted proxy easy) | **P0 — proxy available now** |
| C. Geographic representation | 33% | High (computed from ground truth) | **P0 — can compute now** |
| D. Career narrative arc | ~15% | High (computed from career stats) | **P1 — implement second** |
| E. Defensive dominance | 17% | Medium (captain flag easy, defensive stats hard) | **P1 — partial implementation** |
| F. Aesthetic brilliance | ~8% | Low (dribble data partial) | **P2 — nice to have** |
| G. Voter fatigue | ~8% | High (computed from ground truth) | **P2 — easy to compute** |

---

## The Self-Improving Backtracking Process

Per the user's framing, this is an iterative process:

```
Iteration 1 (current state):
  - Model trained on 21 features
  - 33.9% top-1 LOSO CV, 14.3% held-out
  - Audit identified 10 failure modes
  - Reverse-engineering identified 7 abstract factor categories

Iteration 2 (proposed):
  - Fix Finding 3 (assists/minutes bug) — populate from Understat
  - Add Factor A (tournament heroism) — UCL knockout goals from Understat per-match
  - Add Factor B (tactical engine role) — position-adjusted xG contribution
  - Add Factor C (geographic representation) — nation/continent prior winners
  - Re-validate (LOSO CV only — do NOT touch held-out)
  - Compare metrics. If improved, keep. If not, backtrack.

Iteration 3 (future):
  - Add Factor D (career narrative) — years_in_top_5, first_time_nominee
  - Add Factor E (defensive dominance) — captain_flag, position_rarity
  - Add Factor G (voter fatigue) — years_since_new_winner
  - Re-validate

Iteration 4+ (future):
  - Address Factor F (aesthetic) if data becomes available
  - Add more factors discovered in error analysis of Iteration 3
  - Eventually: fresh held-out evaluation (2026+ seasons)
```

**Critical rule for the backtracking process:** Each iteration's feature additions must be validated on LOSO CV, NOT held-out. The held-out set remains untouched until we have a model we're confident in. Per Key Focus Areas §8, the held-out is a one-shot final check, not an iterative tuning target.

---

## What Makes This Hard (Per User's Acknowledgment)

The user correctly noted this is "a hard and extremely careful task, needing a lot of analytical intelligence." Here's why:

1. **Correlation ≠ causation** — just because geographic representation correlates with missed winners doesn't mean the jury explicitly considers it. Could be confounded with era, league strength, etc.

2. **Small sample size** — 16 missed winners is a tiny sample to identify patterns from. The "33% geographic representation" finding is based on 4-5 winners; could be noise.

3. **Feature interactions** — tournament heroism × position × era may interact in complex ways. A simple linear model can't capture this.

4. **Changing jury composition** — the voter pool changed dramatically over 69 years (Europe-only → global in 2007 → merged with FIFA 2010-2015). Factors that mattered in 1963 may not matter in 2024.

5. **Retroactive quantification bias** — we're identifying factors AFTER seeing who won. There's a risk of overfitting the factor identification to historical winners.

**Mitigation:** Each proposed factor should have a clear football-domain rationale BEFORE checking if it correlates with winners. The rationale comes first, the data second.
