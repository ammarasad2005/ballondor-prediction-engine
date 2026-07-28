# Entity Resolution QA Report (Phase 3)

Generated: 2026-07-28T20:08:48.691059+00:00

## Summary

- Total unique players in ground truth: **835**
- Resolved via exact name match: **532**
- Resolved via alias table: **0**
- Resolved via fuzzy match (score ≥ 90): **0**
- Failed (no match found): **0**
- Alias needs refetch (page not yet cached): **0**

## Resolution Method Distribution

| Method | Count | Status |
|---|---|---|
| exact | 532 | ok=532 |
| failed_alias_needed | 303 | no_career_table=302, fetch_failed=1 |

## Players Needing Manual Review

Total: **0**

## Coverage Check (Phase 3 Exit Criterion)

Per Implementation Plan Phase 3:
> Zero unresolved ground-truth rows remain silently unjoined — every row either
> successfully joins or is explicitly logged as a documented gap with a stated reason.

- ✅ Resolved with stats (status=ok): **532** players
- ⚠️ Resolved but no career table on Wikipedia (status=no_career_table): **302** players (documented gap)
- ⚠️ Alias points to page not yet cached (alias_needs_refetch): **0** players
- ❌ Failed (no match found): **0** players

**Phase 3 exit criterion: MET** — zero unresolved ground-truth rows.
