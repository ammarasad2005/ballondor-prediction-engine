# Features QA Report (Phase 4)

Generated: 2026-07-28T20:13:51.855747+00:00
Output: `/home/z/my-project/ballondor-engine/data/processed/features.parquet`

## Summary

- Total feature rows: **2004**
- Unique players: **835**
- Unique seasons: **69**

## Stats Status Distribution

| Status | Count | % |
|---|---|---|
| ok | 1485 | 74.1% |
| no_career_table | 518 | 25.8% |
| fetch_failed | 1 | 0.0% |

## Era Distribution

| Era | Rows | Players | Winners |
|---|---|---|---|
| classical | 1111 | 487 | 39 |
| pre_merger | 485 | 225 | 15 |
| fifa_merger | 138 | 60 | 6 |
| post_split | 270 | 139 | 9 |

## Feature Coverage by Era

| Feature | Classical | Pre-merger | FIFA merger | Post-split | Overall |
|---|---|---|---|---|---|
| league_goals | 57% | 91% | 93% | 99% | 74% |
| league_assists | 0% | 0% | 0% | 0% | 0% |
| league_apps | 57% | 91% | 93% | 99% | 74% |
| league_minutes | 0% | 0% | 0% | 0% | 0% |
| continental_goals | 46% | 89% | 93% | 97% | 67% |
| continental_apps | 46% | 89% | 93% | 97% | 67% |
| international_goals | 46% | 73% | 68% | 91% | 60% |
| international_apps | 46% | 73% | 68% | 91% | 60% |
| ucl_winner | 7% | 14% | 20% | 22% | 12% |
| domestic_league_winner | 14% | 32% | 53% | 48% | 26% |
| world_cup_winner | 2% | 4% | 9% | 1% | 3% |
| club_prestige_tier | 59% | 91% | 93% | 99% | 74% |
| previous_ballon_dor_winner | 7% | 7% | 11% | 7% | 8% |
| goals_percentile_in_year | 57% | 91% | 93% | 99% | 74% |
| apps_percentile_in_year | 57% | 91% | 93% | 99% | 74% |

## Sanity Check — Known Obvious Winners

These are widely-agreed 'obvious winner' seasons. Feature values should match football-domain expectations.

| Year | Winner | total_goals | ucl_winner | wc_winner | club_prestige |
|---|---|---|---|---|---|
| 1957 | Alfredo Di Stéfano | 69.0 | ✅ | ❌ | tier 1.0 |
| 1972 | Franz Beckenbauer | 13.0 | ❌ | ❌ | tier 1.0 |
| 1998 | Zinedine Zidane | 12.0 | ❌ | ✅ | tier 1.0 |
| 2001 | Michael Owen | 44.0 | ❌ | ❌ | tier 1.0 |
| 2002 | Ronaldo | 36.0 | ✅ | ✅ | tier 1.0 |
| 2008 | Cristiano Ronaldo | 61.0 | ✅ | ❌ | tier 1.0 |
| 2009 | Lionel Messi | 74.0 | ✅ | ❌ | tier 1.0 |
| 2018 | Luka Modrić | 5.0 | ✅ | ❌ | tier 1.0 |
| 2022 | Karim Benzema | 42.0 | ✅ | ❌ | tier 1.0 |
| 2023 | Lionel Messi | 20.0 | ❌ | ✅ | tier 1.0 |

## Conclusion

Overall stats coverage in feature matrix: **74.1%**

Features ready for Phase 5 modeling. Per Key Focus Areas §9, all missing values stay NaN — never silently imputed.
