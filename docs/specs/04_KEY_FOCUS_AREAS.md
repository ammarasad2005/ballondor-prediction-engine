# Ballon d'Or Prediction Engine — Key Focus Areas

This document expands on the areas most likely to determine whether this
project succeeds or silently fails. Architecture and implementation
documents tell the agent *what* to build; this document explains *where
to be most careful and why*, so the agent applies extra scrutiny in the
right places rather than distributing effort evenly.

---

## 1. The Ground Truth Table Is Load-Bearing — Treat It Accordingly

Every feature, every model, every validation number is downstream of
`ground_truth.parquet`. An error here doesn't cause a crash — it causes
a quietly wrong model that looks fine in every metric. Concretely:

- **Eval-period errors are the most dangerous class of bug.** If a
  player's stats are pulled for the wrong window (e.g., full calendar
  year when the actual jury evaluation was season-based, or vice versa),
  the resulting feature values will be *plausible* — just wrong — and
  nothing downstream will flag it as an error. This is why Implementation
  Plan Phase 1 mandates individually verifying every year's eval window
  against two sources, rather than assuming a rule and applying it
  uniformly.
- **The 2010–2015 FIFA Ballon d'Or merger** and the pre-1995 exclusion of
  non-European players from eligibility (the award was originally
  Europe-only, later "European-based players regardless of nationality,"
  later fully global) are historical rule changes that affect who could
  even appear in the candidate pool. If these eligibility-rule shifts are
  not encoded, the model may learn a spurious "pattern" that's actually
  just an artifact of who was allowed to be nominated in a given era.
  **Action:** encode eligibility-rule regime as explicit year-level
  metadata, not just points/rank.

## 2. Entity Resolution Failures Are Silent and Compounding

A misresolved name doesn't error out — it joins to the wrong stats row,
or fails to join at all and gets silently dropped, shrinking your
effective dataset without anyone noticing. Given the small overall N
(~300-350 rows), losing even 10-15 rows to silent join failures is a
meaningful fraction of the dataset.

- Common failure patterns to explicitly guard against: diacritics
  (e.g., a name stored with vs without accent marks across sources),
  mononyms and nickname conventions common in Brazilian/Portuguese
  football, players who share very common names, club name changes
  across decades (sponsorship renames, mergers), and country/federation
  name changes.
- **Action:** the QA report in Implementation Plan Phase 3 must report
  a *count* of ground-truth rows successfully joined vs not, every time
  it's run, and that count must be visually checked against the expected
  total (which is known and small — roughly 5 nominees × ~69 years,
  adjusted for years with different list lengths and the 2020 gap). A
  silent 90% join rate that nobody looks at is worse than no automation
  at all.

## 3. Feature Leakage Is the Single Biggest Threat to Honest Validation

Because the thing being predicted (jury opinion) is entangled with things
that are *consequences* of being a top candidate (media coverage volume,
being described as a "favorite" in pre-ceremony narratives, appearing in
betting-odds-style pundit rankings), it is easy to accidentally build a
feature that already encodes the answer.

- **Concrete leakage risks specific to this domain:**
  - Using post-hoc narrative descriptions ("the standout season," "a
    stellar Ballon d'Or campaign") sourced from articles written *after*
    the winner was known — these will not exist for the actual candidate
    pool of an undecided current season, so a model trained partly on
    this signal will look great historically and fail live.
  - Using betting odds or pundit prediction rankings from close to the
    ceremony date as a feature — these are themselves near-perfect
    proxies for the outcome and would make the model trivially
    "accurate" while adding zero real understanding.
  - Any previous-award-history feature ("won Ballon d'Or before") is
    legitimate as a feature (past winners do get a documented
    reputational boost in voting) but must be lagged correctly — it must
    only reflect awards won *strictly before* the season being evaluated,
    never including the current one.
- **Action:** for every proposed feature, the agent should explicitly ask
  "would this value have been knowable and stable *before* the ceremony
  result was known, for a live, undecided season?" If the honest answer
  is no, the feature does not belong in the model, however predictive it
  looks in backtesting.

## 4. Survivorship Bias in the Candidate Pool

The dataset as scoped only contains the actual top-5 (or however many)
finishers per year — it does not contain the full slate of 20-30 players
who were nominated but finished outside the top 5, and it definitely
doesn't contain the broader universe of "arguably deserving but not even
nominated" players. This has a specific, non-obvious consequence for the
pairwise ranking setup:

- If you only ever train on pairs *within* the historical top-5, the
  model never sees a clear example of "clearly not a contender" vs
  "clearly a contender" — it only ever learns fine-grained distinctions
  among already-elite seasons. This can make it *miscalibrated* for the
  actual real-world task, which is separating a 30-player field down to
  a winner, not just re-ordering a pre-filtered top-5.
- **Mitigation approaches to consider (agent should evaluate feasibility
  given what's actually scrapable):**
  - If a fuller nominee longlist (not just top-5) is sourceable for at
    least some years (France Football has published longer shortlists
    in various eras — worth checking), incorporate it to give the model
    negative examples closer to the real decision boundary.
  - Failing that, construct synthetic/sourced "plausible but non-nominated"
    negative examples for training the pairwise model specifically (e.g.,
    a strong statistical season from a player who was never nominated),
    clearly documented as a deliberate augmentation, not raw historical
    fact — this directly serves the user's "not rigid, should generalize
    outside the data" requirement, since a model that has only ever seen
    top-5-caliber seasons has never learned what separates a contender
    from a non-contender.

## 5. Position and Role Bias — Model It, Don't Just Absorb It

Historically, attacking players (strikers, wingers, attacking
midfielders) are heavily overrepresented among nominees and winners
relative to defenders, defensive midfielders, and goalkeepers. This is a
real, well-documented pattern in the jury's actual behavior — not a data
artifact — so the question is not whether to encode it (P1/P2 mean the
model should reflect actual jury behavior for predictive purposes) but
*how transparently to surface it*.

- **Action:** position should be an explicit feature (not proxy-encoded
  through, say, goals alone, which would conflate "is a defender" with
  "had a bad attacking season"). The explanation layer (Architecture
  Blueprint §4.7) should be able to state plainly when a player's
  predicted rank is being suppressed primarily by positional base rate
  rather than individual performance — this is the honest, transparent
  version of P5 (explicit bias handling, not silent bias).
- This also directly matters for the "not rigid" goal: a purely
  attacker-trained model would likely fail badly if a future season
  produces an unusually dominant defensive player, whereas a model that
  explicitly separates "position effect" from "performance-within-position
  percentile" can still say something sensible about that case.

## 6. Recency/Narrative-Timing Bias Should Be a Feature, Not Noise

As discussed in the initial project scoping, voters are documented to
weight the second half of a season/year more heavily than the first —
a player who is exceptional through May but fades by August often loses
ground to a player who peaks in September–November right before voting.
This is not something to "correct for" in pursuit of a more "objective"
model — for a model whose explicit goal is *predicting what the jury
will actually do*, this is signal.

- **Action:** build an explicit recency-weighted form feature (as
  specified in Architecture Blueprint §4.4 family 6) rather than only
  using flat full-period aggregates. Validate its value empirically —
  if it doesn't improve held-out validation metrics, that's a real
  finding worth reporting (maybe the bias is weaker or more inconsistent
  than assumed), not a reason to force it in anyway.

## 7. Cross-Era Comparability

Football has changed pace, tactics, and statistical norms significantly
over 69 years — a 30-goal season means something different in 1966 than
in 2024, both because of tactical eras and because of competition volume
(European competitions have expanded significantly; a modern top player
plays far more high-level matches per season than a 1960s equivalent).

- **Action:** wherever possible, prefer **peer-relative (percentile-
  within-season) features** over raw magnitude features for anything
  that's likely to drift over time (goals, assists, minutes). This is
  already specified as feature family 5 in the Architecture Blueprint —
  this section is flagging *why* it matters enough to prioritize: it is
  probably the single highest-leverage design choice for making the
  model transfer across the classical/modern era split described in P4,
  since it sidesteps needing to model football's statistical inflation
  directly.

## 8. Validation Discipline Is What Makes This "Not Rigid"

The user's stated core concern — a solution "shouldn't be exactly
rigid... built on the data so much that it learns the data and couldn't
perform outside that data" — is a textbook overfitting concern, and the
single most important structural defense against it is validation
discipline, not model choice. A simple, well-validated linear model is
less rigid in the meaningful sense than a complex model evaluated only
on training data, regardless of which one is more "sophisticated."

- **Action, restated for emphasis because it is the crux of the whole
  project:** never let a design decision (feature choice, hyperparameter,
  model tier selection) be made by looking at performance on the final
  held-out test seasons. Those seasons exist to answer one question,
  once, at the very end: "does this generalize." If the temptation arises
  to peek and adjust, that is the exact failure mode the user is asking
  to be protected against, and the agent should treat that temptation as
  a hard stop signal, not a minor process shortcut.

## 9. Data Gaps Should Be Visible, Never Silently Filled

Especially in the classical era, some data will simply not be
crawlable/available (assist counts pre-1990s, minutes played in early
decades, advanced metrics before their invention). The temptation will be
to impute, estimate, or approximate these gaps to keep the pipeline
running smoothly.

- **Action:** distinguish clearly, in both code (a `_is_imputed` /
  `_is_missing` companion column convention) and in the
  `PROJECT_LOG.md`/validation report narrative, between (a) real sourced
  values, (b) deliberately and transparently imputed values (e.g.,
  "median for era/position" as a documented fallback), and (c) features
  simply unavailable and excluded for that era. Never let (b) silently
  look like (a) in a downstream report — this is both a scientific
  integrity issue and a direct threat to P2 (a model "generalizing" well
  partly because of silently-imputed, near-constant filler values across
  many classical-era rows is not actually generalizing).

## 10. Explanation Quality Is a Deliverable, Not a Nice-to-Have

The user's long-term goal is "thoroughly understanding the jury's past
actions" — the explanatory layer (why does player X rank where they do)
is arguably as important as raw ranking accuracy for this specific
project's purpose, which is closer to analytical understanding than to a
pure prediction leaderboard.

- **Action:** treat Architecture Blueprint §4.7's per-candidate
  contribution breakdown as a first-class output, tested and reviewed
  with the same rigor as ranking accuracy — not an afterthought bolted
  on at the end. When the agent reaches Implementation Plan Phase 7's
  manual spot check, the explanation output should be judged not just on
  "is the ranking plausible" but "does the stated reasoning for each
  ranking match what a knowledgeable football follower would actually
  cite as that player's case for/against."
