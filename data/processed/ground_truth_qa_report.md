# Ground Truth QA Report

Generated: 2026-07-28T14:52:04.651398+00:00
Input: `/home/z/my-project/ballondor-engine/data/raw/ground_truth/nominees_raw.jsonl`
Output: `/home/z/my-project/ballondor-engine/data/processed/ground_truth.parquet`

## Summary

- Total rows: **2004**
- Unique years: **69** (expected: 69)
- Year range: **1956-2025**
- Rows per year: min=19, median=30, max=50, mean=29.0
- Total winners (rank=1): **69** (expected: 69)

## QA Checks

| Check | Anomalies | Status |
|---|---|---|
| years_with_no_winner | 0 | ✅ PASS |
| years_with_multiple_winners | 0 | ✅ PASS |
| rank_gaps | 0 | ✅ PASS |
| duplicate_players_in_year | 0 | ✅ PASS |
| points_not_nonincreasing | 0 | ✅ PASS |
| missing_years | 0 | ✅ PASS |
| extra_years | 0 | ✅ PASS |
| missing_player_name | 0 | ✅ PASS |
| missing_club | 0 | ✅ PASS |
| missing_nationality | 5 | ⚠️ 5 anomalies |
| missing_points | 2 | ⚠️ 2 anomalies |
| low_row_count_years | 0 | ✅ PASS |

## Anomaly Details

### years_with_no_winner

No anomalies.

### years_with_multiple_winners

No anomalies.

### rank_gaps

No anomalies.

### duplicate_players_in_year

No anomalies.

### points_not_nonincreasing

No anomalies.

### missing_years

No anomalies.

### extra_years

No anomalies.

### missing_player_name

No anomalies.

### missing_club

No anomalies.

### missing_nationality

Found 5 anomalies:

- `{'year': 2016, 'count': 30}`
- `{'year': 2017, 'count': 30}`
- `{'year': 2018, 'count': 30}`
- `{'year': 2019, 'count': 30}`
- `{'year': 2021, 'count': 30}`

### missing_points

Found 2 anomalies:

- `{'year': 1996, 'count': 4}`
- `{'year': 2018, 'count': 1}`

### low_row_count_years

No anomalies.

## Era Breakdown

| Era | Years | Rows | Winners | Avg rows/year |
|---|---|---|---|---|
| Classical (1956-1994) | 39 | 1111 | 39 | 28.5 |
| Pre-merger (1995-2009) | 15 | 485 | 15 | 32.3 |
| FIFA merger (2010-2015) | 6 | 138 | 6 | 23.0 |
| Post-split (2016-present) | 9 | 270 | 9 | 30.0 |

## Sample Winners by Era

| Year | Winner | Club | Nationality | Points |
|---|---|---|---|---|
| 1956 | Stanley Matthews | Blackpool | England | 47.0 |
| 1966 | Bobby Charlton | Manchester United | England | 81.0 |
| 1976 | Franz Beckenbauer | Bayern Munich | West Germany | 91.0 |
| 1986 | Igor Belanov | Dynamo Kyiv | Soviet Union | 84.0 |
| 1996 | Matthias Sammer | Borussia Dortmund | Germany | 144.0 |
| 2006 | Fabio Cannavaro | Juventus Real Madrid | Italy | 173.0 |
| 2010 | Lionel Messi | Barcelona | Argentina | 22.65 |
| 2015 | Lionel Messi | Barcelona | Argentina | 41.33 |
| 2018 | Luka Modrić | Real Madrid |  | nan |
| 2021 | Lionel Messi | Paris Saint-Germain |  | 613.0 |
| 2024 | Rodri | Manchester City | Spain | 1170.0 |
