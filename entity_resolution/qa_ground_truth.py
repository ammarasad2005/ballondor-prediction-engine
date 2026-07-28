"""QA pass + build ground_truth.parquet for Phase 1.

Per Implementation Plan Phase 1 task 4:
  Run an internal consistency QA pass: every year has a winner, rank-1
  through rank-N are contiguous with no gaps, no duplicate players within
  a year, points (where available) are non-increasing with rank.

Per Architecture Blueprint §4.1 schema, ground_truth.parquet has:
  season_id, award_year, eval_period_start, eval_period_end, rank,
  player_name_raw, player_name_canonical, club_at_time, nation_team,
  points, source

The player_name_canonical field is populated later (Phase 3 entity
resolution). The eval_period_start/end fields are populated from
data/processed/eval_windows.yaml (built by build_eval_windows.py).

Per Key Focus Areas §9, missing data must be visible:
  - rows with no points → points column stays NaN (not 0)
  - rows with no nationality (1956-2009 era mostly) → empty string
  - the parquet preserves NaN/None for downstream visibility
"""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
INPUT_JSONL = PROJECT_ROOT / "data" / "raw" / "ground_truth" / "nominees_raw.jsonl"
EVAL_WINDOWS_YAML = PROJECT_ROOT / "data" / "processed" / "eval_windows.yaml"
OUTPUT_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"
QA_REPORT = PROJECT_ROOT / "data" / "processed" / "ground_truth_qa_report.md"

EXPECTED_YEARS = sorted(set(range(1956, 2026)) - {2020})  # 1956-2025 excl 2020


def load_raw() -> pd.DataFrame:
    rows = []
    with open(INPUT_JSONL, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return pd.DataFrame(rows)


def qa_pass(df: pd.DataFrame) -> dict:
    """Run all QA checks. Returns dict of check_name -> list of anomaly dicts."""
    anomalies: dict[str, list] = {
        "years_with_no_winner": [],
        "years_with_multiple_winners": [],
        "rank_gaps": [],            # non-contiguous ranks within a year
        "duplicate_players_in_year": [],
        "points_not_nonincreasing": [],
        "missing_years": [],
        "extra_years": [],
        "missing_player_name": [],
        "missing_club": [],
        "missing_nationality": [],
        "missing_points": [],
        "low_row_count_years": [],  # <10 rows (suspicious — possibly truncated)
    }

    # 1. Year coverage
    actual_years = set(df["award_year"].astype(int).unique())
    expected = set(EXPECTED_YEARS)
    missing = sorted(expected - actual_years)
    extra = sorted(actual_years - expected)
    for y in missing:
        anomalies["missing_years"].append({"year": y})
    for y in extra:
        anomalies["extra_years"].append({"year": y})

    # 2. Per-year checks
    for year, group in df.groupby("award_year"):
        year = int(year)
        n_rows = len(group)

        # Winner check
        winners = group[group["rank"] == 1]
        if len(winners) == 0:
            anomalies["years_with_no_winner"].append({"year": year, "n_rows": n_rows})
        elif len(winners) > 1:
            anomalies["years_with_multiple_winners"].append({
                "year": year, "n_winners": len(winners),
                "winners": winners["player_name_raw"].tolist()
            })

        # Rank contiguity — Ballon d'Or uses COMPETITION ranking (1, 2, 2, 4...)
        # where tied players share a rank. So gaps ARE expected and don't
        # indicate data errors. We flag only SUSPICIOUS gaps: where the max
        # rank exceeds n_rows × 1.5 (suggesting a parsing bug rather than ties).
        ranks = sorted(group["rank"].unique().astype(int))
        max_rank = max(ranks) if ranks else 0
        if max_rank > n_rows * 1.5:
            anomalies["rank_gaps"].append({
                "year": year, "n_rows": n_rows, "max_rank": max_rank,
                "ranks_found": ranks[:20],
                "ranks_missing_from_contiguous": sorted(set(range(1, max_rank + 1)) - set(ranks))[:10],
            })

        # Duplicate players within year
        player_counts = group["player_name_raw"].value_counts()
        dups = player_counts[player_counts > 1]
        if len(dups) > 0:
            anomalies["duplicate_players_in_year"].append({
                "year": year, "duplicates": dups.to_dict()
            })

        # Points non-increasing with rank
        with_points = group.dropna(subset=["points"]).sort_values("rank")
        if len(with_points) >= 2:
            points_list = with_points["points"].tolist()
            for i in range(len(points_list) - 1):
                if points_list[i] < points_list[i + 1]:
                    anomalies["points_not_nonincreasing"].append({
                        "year": year,
                        "rank_a": int(with_points.iloc[i]["rank"]),
                        "player_a": with_points.iloc[i]["player_name_raw"],
                        "points_a": float(points_list[i]),
                        "rank_b": int(with_points.iloc[i + 1]["rank"]),
                        "player_b": with_points.iloc[i + 1]["player_name_raw"],
                        "points_b": float(points_list[i + 1]),
                    })
                    break  # one anomaly per year is enough

        # Missing field counts
        missing_player = group["player_name_raw"].isna().sum() + (group["player_name_raw"] == "").sum()
        missing_club = group["club_at_time"].isna().sum() + (group["club_at_time"] == "").sum()
        missing_nat = group["nation_team"].isna().sum() + (group["nation_team"] == "").sum()
        missing_pts = group["points"].isna().sum()

        if missing_player > 0:
            anomalies["missing_player_name"].append({"year": year, "count": int(missing_player)})
        if missing_club > 0:
            anomalies["missing_club"].append({"year": year, "count": int(missing_club)})
        if missing_nat > 0:
            anomalies["missing_nationality"].append({"year": year, "count": int(missing_nat)})
        if missing_pts > 0:
            anomalies["missing_points"].append({"year": year, "count": int(missing_pts)})

        # Low row count
        if n_rows < 10:
            anomalies["low_row_count_years"].append({"year": year, "n_rows": n_rows})

    return anomalies


def write_qa_report(df: pd.DataFrame, anomalies: dict) -> None:
    lines = []
    lines.append("# Ground Truth QA Report\n\n")
    lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n")
    lines.append(f"Input: `{INPUT_JSONL}`\n")
    lines.append(f"Output: `{OUTPUT_PARQUET}`\n\n")

    lines.append("## Summary\n\n")
    lines.append(f"- Total rows: **{len(df)}**\n")
    lines.append(f"- Unique years: **{df['award_year'].nunique()}** (expected: {len(EXPECTED_YEARS)})\n")
    lines.append(f"- Year range: **{df['award_year'].min()}-{df['award_year'].max()}**\n")
    sizes = df.groupby("award_year").size()
    lines.append(f"- Rows per year: min={sizes.min()}, median={sizes.median():.0f}, max={sizes.max()}, mean={sizes.mean():.1f}\n")
    lines.append(f"- Total winners (rank=1): **{(df['rank'] == 1).sum()}** (expected: {len(EXPECTED_YEARS)})\n\n")

    lines.append("## QA Checks\n\n")
    lines.append("| Check | Anomalies | Status |\n|---|---|---|\n")
    for check, items in anomalies.items():
        status = "✅ PASS" if not items else f"⚠️ {len(items)} anomalies"
        lines.append(f"| {check} | {len(items)} | {status} |\n")
    lines.append("\n")

    lines.append("## Anomaly Details\n\n")
    for check, items in anomalies.items():
        if not items:
            lines.append(f"### {check}\n\nNo anomalies.\n\n")
            continue
        lines.append(f"### {check}\n\n")
        lines.append(f"Found {len(items)} anomalies:\n\n")
        # Cap detail to first 20 anomalies per check for readability
        for item in items[:20]:
            lines.append(f"- `{item}`\n")
        if len(items) > 20:
            lines.append(f"- ... and {len(items) - 20} more\n")
        lines.append("\n")

    # Era breakdown — useful sanity check
    lines.append("## Era Breakdown\n\n")
    lines.append("| Era | Years | Rows | Winners | Avg rows/year |\n|---|---|---|---|---|\n")
    eras = [
        ("Classical (1956-1994)", range(1956, 1995)),
        ("Pre-merger (1995-2009)", range(1995, 2010)),
        ("FIFA merger (2010-2015)", range(2010, 2016)),
        ("Post-split (2016-present)", range(2016, 2026)),
    ]
    for label, yr_range in eras:
        yrs_in_range = [y for y in yr_range if y != 2020]
        sub = df[df["award_year"].isin(yrs_in_range)]
        n_yrs = sub["award_year"].nunique()
        n_rows = len(sub)
        n_winners = (sub["rank"] == 1).sum()
        avg = n_rows / n_yrs if n_yrs else 0
        lines.append(f"| {label} | {n_yrs} | {n_rows} | {n_winners} | {avg:.1f} |\n")

    # Sample winners per era
    lines.append("\n## Sample Winners by Era\n\n")
    lines.append("| Year | Winner | Club | Nationality | Points |\n|---|---|---|---|---|\n")
    sample_years = [1956, 1966, 1976, 1986, 1996, 2006, 2010, 2015, 2018, 2021, 2024]
    for y in sample_years:
        sub = df[(df["award_year"] == y) & (df["rank"] == 1)]
        if len(sub):
            r = sub.iloc[0]
            lines.append(f"| {y} | {r['player_name_raw']} | {r['club_at_time']} | {r['nation_team']} | {r['points']} |\n")

    QA_REPORT.parent.mkdir(parents=True, exist_ok=True)
    QA_REPORT.write_text("".join(lines), encoding="utf-8")
    print(f"✅ Wrote QA report to {QA_REPORT}")


def build_parquet(df: pd.DataFrame) -> None:
    """Build ground_truth.parquet per Architecture Blueprint §4.1 schema.

    The canonical name field is populated later (Phase 3 entity resolution).
    The eval_period_start/end fields are populated from eval_windows.yaml
    (built by build_eval_windows.py).
    """
    # Load eval windows
    if EVAL_WINDOWS_YAML.exists():
        with open(EVAL_WINDOWS_YAML, encoding="utf-8") as f:
            eval_windows = yaml.safe_load(f) or {}
    else:
        eval_windows = {}
        print(f"⚠️ Eval windows YAML not found at {EVAL_WINDOWS_YAML}; eval_period fields will be empty")

    # Map eval windows onto each row by season_id
    df_out = df.copy()
    df_out["player_name_canonical"] = None  # populated in Phase 3
    df_out["eval_period_start"] = df_out["season_id"].map(
        lambda sid: eval_windows.get(sid, {}).get("eval_period_start")
    )
    df_out["eval_period_end"] = df_out["season_id"].map(
        lambda sid: eval_windows.get(sid, {}).get("eval_period_end")
    )
    df_out["eval_period_type"] = df_out["season_id"].map(
        lambda sid: eval_windows.get(sid, {}).get("eval_period_type")
    )

    # Enforce schema column order per Architecture Blueprint §4.1
    schema_cols = [
        "season_id", "award_year",
        "eval_period_start", "eval_period_end", "eval_period_type",
        "rank", "player_name_raw", "player_name_canonical",
        "club_at_time", "nation_team", "position_raw",
        "points", "source",
    ]
    df_out = df_out[[c for c in schema_cols if c in df_out.columns]]

    OUTPUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    # Use pyarrow to write — preserves dtypes (especially nullable int/float)
    table = pa.Table.from_pandas(df_out, preserve_index=False)
    pq.write_table(table, OUTPUT_PARQUET, compression="snappy")
    print(f"✅ Wrote {OUTPUT_PARQUET} ({len(df_out)} rows, {df_out.shape[1]} cols)")

    # Print eval period coverage
    n_with_eval = df_out["eval_period_start"].notna().sum()
    print(f"   Rows with eval_period populated: {n_with_eval}/{len(df_out)}")


def main() -> None:
    df = load_raw()
    print(f"Loaded {len(df)} rows from {INPUT_JSONL}")

    anomalies = qa_pass(df)
    write_qa_report(df, anomalies)

    build_parquet(df)

    # Print summary to stdout
    print()
    print("=" * 70)
    print("QA Summary")
    print("=" * 70)
    for check, items in anomalies.items():
        status = "✅" if not items else f"⚠️ {len(items)}"
        print(f"  {status}  {check}")
    print()
    n_anomalies_total = sum(len(v) for v in anomalies.values())
    print(f"Total anomalies: {n_anomalies_total}")


if __name__ == "__main__":
    main()
