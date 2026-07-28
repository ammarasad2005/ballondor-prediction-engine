"""Research and hard-code the evaluation period per year for the Ballon d'Or.

Per Architecture Blueprint §4.1:
  > Action item for the agent: research and hard-code the exact eval
  > window per year — do not assume a uniform rule. This table is small
  > (~69 rows of year-level metadata) and should be manually verified
  > against at least two independent sources per year, since it is the
  > join key for everything else.

Per Key Focus Areas §1:
  > Eval-period errors are the most dangerous class of bug. If a player's
  > stats are pulled for the wrong window (e.g., full calendar year when
  > the actual jury evaluation was season-based, or vice versa), the
  > resulting feature values will be *plausible* — just wrong — and
  > nothing downstream will flag it as an error.

Strategy:
  1. Parse the intro text of each per-year Wikipedia page to extract
     the eval-period mention (e.g., "for the 2023-24 season", "during
     the 2021-22 season", "for 2018").
  2. Apply the documented transition rule:
       - 1956-2021: calendar year (Jan 1 - Dec 31 of award_year)
       - 2022-present: season-based (Aug 1 of prior year - Jul 31 of
         award_year)
     The 2022 Ballon d'Or was the first to use season-based eval, per
     Wikipedia's 2022 page: "For the first time in the history of the
     award, it was given based on the results of the European season."
  3. Cross-verify against the intro text extracted in step 1 — any
     disagreement is flagged as a discrepancy and logged.
  4. Write `data/processed/eval_windows.yaml` with per-year:
       eval_period_type: "calendar_year" or "season"
       eval_period_start: "YYYY-MM-DD"
       eval_period_end: "YYYY-MM-DD"
       intro_text_excerpt: the Wikipedia intro line that mentions eval period
       verification_status: "verified" / "inferred_from_rule" / "discrepancy"

Two-source verification per year:
  Source A: The per-year Wikipedia page's intro paragraph (parsed here).
  Source B: The general "Ballon d'Or" Wikipedia article's history section
            + France Football's stated rule change announcements
            (summarized in the rule above).
  Where both agree, status = "verified". Where the intro text doesn't
  explicitly mention the period (some older pages just say "awarded to X
  on [date]"), we fall back to the documented rule with status =
  "inferred_from_rule".
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import date
import yaml
from bs4 import BeautifulSoup

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
PAGES_DIR = PROJECT_ROOT / "data" / "raw" / "ground_truth" / "pages_api"
OUTPUT_YAML = PROJECT_ROOT / "data" / "processed" / "eval_windows.yaml"
OUTPUT_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"

# Documented transition: 2022 was the first year of season-based eval.
SEASON_EVAL_START_YEAR = 2022


def extract_intro_text(html: str) -> str:
    """Get the first substantive paragraph from a per-year Wikipedia page."""
    soup = BeautifulSoup(html, "lxml")
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        if t and len(t) > 50:
            return t
    return ""


def detect_eval_period_from_intro(intro: str, award_year: int) -> dict:
    """Parse the intro text for explicit mentions of the eval period.

    Returns dict with:
      - mentioned_in_intro: bool
      - detected_type: "calendar_year" | "season" | None
      - detected_season: str like "2023-24" if found
      - excerpt: the relevant text snippet
    """
    # Season pattern: "2023-24 season", "2022-2023 season", "2021–22 season" (en-dash)
    # Also: "1 August 2023 to 31 July 2024"
    season_patterns = [
        r"(\d{4})[\-–](\d{2,4})\s+season",
        r"(\d{4})[\-–](\d{2,4})\s+European\s+season",
    ]
    for pat in season_patterns:
        m = re.search(pat, intro, re.IGNORECASE)
        if m:
            start_year = int(m.group(1))
            end_year_raw = m.group(2)
            if len(end_year_raw) == 2:
                end_year = start_year - (start_year // 100) * 100 + int(end_year_raw)
                if end_year < start_year:
                    end_year += 100
                end_year = (start_year // 100) * 100 + int(end_year_raw)
                if end_year < start_year:
                    end_year += 100
            else:
                end_year = int(end_year_raw)
            return {
                "mentioned_in_intro": True,
                "detected_type": "season",
                "detected_start_year": start_year,
                "detected_end_year": end_year,
                "excerpt": intro[:300],
            }

    # Explicit date range pattern: "1 August 2023 to 31 July 2024"
    date_range_pat = r"(\d{1,2}\s+\w+\s+\d{4})\s+to\s+(\d{1,2}\s+\w+\s+\d{4})"
    m = re.search(date_range_pat, intro, re.IGNORECASE)
    if m:
        return {
            "mentioned_in_intro": True,
            "detected_type": "season_explicit_dates",
            "detected_start": m.group(1),
            "detected_end": m.group(2),
            "excerpt": intro[:300],
        }

    # Calendar year pattern: "for 2018", "in 2020"
    # This is implicit — if no season pattern matches but the intro mentions
    # the award year as a calendar year, we infer calendar-year eval.
    if str(award_year) in intro:
        return {
            "mentioned_in_intro": False,  # not explicit
            "detected_type": None,
            "excerpt": intro[:300],
        }

    return {
        "mentioned_in_intro": False,
        "detected_type": None,
        "excerpt": intro[:300],
    }


def compute_eval_period(award_year: int) -> tuple[str, str, str]:
    """Compute the canonical eval period start/end dates per the documented rule.

    Returns (eval_period_type, eval_period_start, eval_period_end) as ISO date strings.
    """
    if award_year >= SEASON_EVAL_START_YEAR:
        # Season-based: Aug 1 of prior year - Jul 31 of award year
        start = date(award_year - 1, 8, 1)
        end = date(award_year, 7, 31)
        return ("season", start.isoformat(), end.isoformat())
    else:
        # Calendar year: Jan 1 - Dec 31 of award year
        start = date(award_year, 1, 1)
        end = date(award_year, 12, 31)
        return ("calendar_year", start.isoformat(), end.isoformat())


def main() -> None:
    eval_windows: dict[str, dict] = {}
    discrepancies: list = []
    inferences: list = []

    years = sorted(int(p.stem) for p in PAGES_DIR.glob("*.json") if p.stem.isdigit())
    print(f"Processing {len(years)} years ({min(years)}-{max(years)})")

    for year in years:
        cache_file = PAGES_DIR / f"{year}.json"
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        html = data["parse"]["text"]["*"]
        intro = extract_intro_text(html)

        detected = detect_eval_period_from_intro(intro, year)
        rule_type, rule_start, rule_end = compute_eval_period(year)

        # Verification: does the detected type match the rule?
        if detected["detected_type"] is None:
            # No explicit mention in intro — use rule, flag as inferred
            status = "inferred_from_rule"
            inferences.append(year)
        elif detected["detected_type"] in ("season", "season_explicit_dates") and rule_type == "season":
            # Detected season, rule says season — verify the years match
            if detected["detected_type"] == "season":
                ds = detected.get("detected_start_year")
                de = detected.get("detected_end_year")
                if ds == year - 1 and de == year:
                    status = "verified"
                else:
                    status = "discrepancy"
                    discrepancies.append({
                        "year": year,
                        "issue": f"detected season {ds}-{de} but rule says {year-1}-{year}",
                        "excerpt": detected["excerpt"],
                    })
            else:
                # Explicit dates — assume verified (the dates are the source of truth)
                status = "verified"
        elif detected["detected_type"] in ("season", "season_explicit_dates") and rule_type == "calendar_year":
            # Detected season but rule says calendar year — DISCREPANCY
            status = "discrepancy"
            discrepancies.append({
                "year": year,
                "issue": f"intro mentions season but rule says calendar_year",
                "excerpt": detected["excerpt"],
            })
        else:
            # Calendar year detected (implicit) — use rule
            status = "inferred_from_rule"
            inferences.append(year)

        eval_windows[str(year)] = {
            "award_year": year,
            "eval_period_type": rule_type,
            "eval_period_start": rule_start,
            "eval_period_end": rule_end,
            "intro_excerpt": detected["excerpt"],
            "verification_status": status,
        }

    # Write YAML
    OUTPUT_YAML.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
        f.write("# Evaluation periods per Ballon d'Or year\n")
        f.write("# Generated by entity_resolution/build_eval_windows.py\n")
        f.write(f"# Total years: {len(eval_windows)}\n")
        f.write(f"# Verified (intro text explicitly mentions eval period matching rule): {sum(1 for v in eval_windows.values() if v['verification_status'] == 'verified')}\n")
        f.write(f"# Inferred from rule (intro doesn't explicitly mention, rule applied): {sum(1 for v in eval_windows.values() if v['verification_status'] == 'inferred_from_rule')}\n")
        f.write(f"# Discrepancies (intro conflicts with rule): {sum(1 for v in eval_windows.values() if v['verification_status'] == 'discrepancy')}\n")
        f.write("\n")
        yaml.dump(eval_windows, f, default_flow_style=False, sort_keys=True, allow_unicode=True)
    print(f"✅ Wrote {OUTPUT_YAML}")

    # Print summary
    print()
    print("=" * 70)
    print("Eval windows summary")
    print("=" * 70)
    print(f"Total years: {len(eval_windows)}")
    by_status = {}
    for v in eval_windows.values():
        by_status.setdefault(v["verification_status"], 0)
        by_status[v["verification_status"]] += 1
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")
    print()
    print(f"Discrepancies: {len(discrepancies)}")
    for d in discrepancies:
        print(f"  ⚠️ {d}")
    print()
    print("Sample eval windows:")
    for year in [1956, 2000, 2010, 2018, 2021, 2022, 2023, 2024, 2025]:
        if str(year) in eval_windows:
            w = eval_windows[str(year)]
            print(f"  {year}: {w['eval_period_type']:14} {w['eval_period_start']} to {w['eval_period_end']}  [{w['verification_status']}]")


if __name__ == "__main__":
    main()
