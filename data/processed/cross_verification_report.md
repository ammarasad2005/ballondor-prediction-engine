# Cross-Verification Report — Random 10-Year Sample

Generated: 2026-07-28T14:50:18.818227+00:00

## Methodology

Per Implementation Plan Phase 1 task 5, a stratified random sample of 10 years
spanning all eras was cross-verified against a second source.

**Primary source (under test):** `data/raw/ground_truth/nominees_raw.jsonl`
  — parsed from per-year Wikipedia Action API pages (`{year}_Ballon_d'Or`).

**Cross-check source:** Main Wikipedia Ballon d'Or page's historical
  top-3-per-year table (`https://en.wikipedia.org/wiki/Ballon_d%27Or`, table[2]).
  This is a separate page compilation (curated summary table vs per-year article
  body) maintained independently on Wikipedia.

**Acknowledged limitation:** Both sources are Wikipedia, so this is not a fully
independent cross-source verification. RSSSF returns 404 for Ballon d'Or pages,
and France Football archives are paywalled. The main-page table is the best
available free cross-check in this sandbox. A documented gap, per Key Focus
Areas §9 — not a silent pass-through.

**Comparison rule:** Player names normalized via lowercasing, accent stripping
(NFD decomposition), footnote-marker removal, and parenthetical award-count
removal (e.g. 'Lionel Messi (2)' → 'lionel messi'). A match means the normalized
names are identical.

## Sample Years

Stratified random sample: [1956, 1972, 1985, 1993, 2001, 2008, 2011, 2014, 2019, 2024]

| Year | Era | Rank | My parse | Main page | Match? |
|---|---|---|---|---|---|
| 1956 | Classical | 1 | Stanley Matthews | Stanley Matthews | ✅ |
| 1956 | Classical | 2 | Alfredo Di Stéfano | Alfredo Di Stéfano | ✅ |
| 1956 | Classical | 3 | Raymond Kopa | Raymond Kopa | ✅ |
| 1972 | Classical | 1 | Franz Beckenbauer | Franz Beckenbauer (1) | ✅ |
| 1972 | Classical | 2 | Günter Netzer | Günter Netzer | ✅ |
| 1985 | Classical | 1 | Michel Platini | Michel Platini (3) | ✅ |
| 1985 | Classical | 2 | Preben Elkjær | Preben Elkjær | ✅ |
| 1985 | Classical | 3 | Bernd Schuster | Bernd Schuster | ✅ |
| 1993 | Classical | 1 | Roberto Baggio | Roberto Baggio | ✅ |
| 1993 | Classical | 2 | Dennis Bergkamp | Dennis Bergkamp | ✅ |
| 1993 | Classical | 3 | Eric Cantona | Eric Cantona | ✅ |
| 2001 | Pre-merger | 1 | Michael Owen | Michael Owen | ✅ |
| 2001 | Pre-merger | 2 | Raúl | Raúl | ✅ |
| 2001 | Pre-merger | 3 | Oliver Kahn | Oliver Kahn | ✅ |
| 2008 | Pre-merger | 1 | Cristiano Ronaldo | Cristiano Ronaldo (1) | ✅ |
| 2008 | Pre-merger | 2 | Lionel Messi | Lionel Messi | ✅ |
| 2008 | Pre-merger | 3 | Fernando Torres | Fernando Torres | ✅ |
| 2011 | FIFA merger | 1 | Lionel Messi | Lionel Messi (3) | ✅ |
| 2011 | FIFA merger | 2 | Cristiano Ronaldo | Cristiano Ronaldo | ✅ |
| 2011 | FIFA merger | 3 | Xavi | Xavi | ✅ |
| 2014 | FIFA merger | 1 | Cristiano Ronaldo | Cristiano Ronaldo (3) | ✅ |
| 2014 | FIFA merger | 2 | Lionel Messi | Lionel Messi | ✅ |
| 2014 | FIFA merger | 3 | Manuel Neuer | Manuel Neuer | ✅ |
| 2019 | Post-split | 1 | Lionel Messi | Lionel Messi (6) | ✅ |
| 2019 | Post-split | 2 | Virgil van Dijk | Virgil van Dijk | ✅ |
| 2019 | Post-split | 3 | Cristiano Ronaldo | Cristiano Ronaldo | ✅ |
| 2024 | Post-split | 1 | Rodri | Rodri | ✅ |
| 2024 | Post-split | 2 | Vinícius Júnior | Vinícius Júnior | ✅ |
| 2024 | Post-split | 3 | Jude Bellingham | Jude Bellingham | ✅ |

## Summary

- Total comparisons: 29
- ✅ Matches: 29
- ❌ Mismatches: 0
- ⚠️ Main page missing entries: 0
- ❌ My parse missing entries: 0
- Match rate: 100.0%

## Conclusion

✅ **All 10 sample years pass cross-verification.** Top-3 player names
from my per-year-page parse match the main Wikipedia Ballon d'Or page's
historical top-3 table for every sampled rank, across all four eras
(classical, pre-merger, FIFA merger, post-split). Per-year-page parsing
is verified sound.
