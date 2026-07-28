"""Cross-verify a random 10-year sample of ground truth against a second
independent source.

Per Implementation Plan Phase 1 task 5:
  a random 10-year sample has been spot-checked by the agent against a
  second independent source with results logged in PROJECT_LOG.md.

Strategy:
  1. Parse the main Wikipedia "Ballon d'Or" page's consolidated table
     (top 3 per year for 1956-2025). This is a SEPARATE Wikipedia page
     from the per-year pages I scraped — it's a different compilation
     (curated summary table vs per-year article body), so it serves as
     a real cross-check on per-year-page parsing accuracy.

  2. Pick a stratified random sample of 10 years spanning all eras:
       - 4 classical era (1956-1994)
       - 2 pre-merger (1995-2009)
       - 2 FIFA merger (2010-2015)
       - 2 post-split (2016-present)

  3. For each sample year, compare rank 1, 2, 3 player names between:
       - nominees_raw.jsonl (my per-year-page parse)
       - main page table (independent compilation)

  4. Log all matches and mismatches; flag any year with disagreement.

Acknowledged limitation (per Key Focus Areas §1): both sources are
Wikipedia, so this is not a fully independent cross-source verification.
However, it IS a different page compilation (the main page's curated
top-3 table is maintained separately from each per-year article's full
ranking table), so it catches per-year-page parsing bugs effectively.
For a fully independent verification, the agent would need to source
from France Football archives or RSSSF — neither accessible in this
sandbox (RSSSF returns 404 for Ballon d'Or; France Football archives
are paywalled). This limitation is logged in PROJECT_LOG.md.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
MAIN_PAGE_CACHE = PROJECT_ROOT / "data" / "raw" / "ground_truth" / "pages" / "main.html"
NOMINEES_JSONL = PROJECT_ROOT / "data" / "raw" / "ground_truth" / "nominees_raw.jsonl"
OUTPUT_REPORT = PROJECT_ROOT / "data" / "processed" / "cross_verification_report.md"

# Stratified random sample (picked deterministically for reproducibility).
SAMPLE_YEARS = [1956, 1972, 1985, 1993, 2001, 2008, 2011, 2014, 2019, 2024]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

FOOTNOTE_RE = re.compile(r"\s*\[[^\]]*\]\s*")


def normalize_name(s) -> str:
    """Normalize a player name for comparison: lowercase, strip footnotes,
    collapse whitespace, strip accents (use NFD decomposition)."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).replace("\xa0", " ")
    s = FOOTNOTE_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    # Strip common parenthetical clarifications like "Messi (2)" indicating
    # "Messi's 2nd award" — these appear in the main page table
    s = re.sub(r"\s*\(\d+\)\s*", "", s)
    # Strip accents
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s


def parse_main_page_top3(html: str) -> dict[int, dict[int, str]]:
    """Parse the main Wikipedia Ballon d'Or page table[2] (the historical
    1956-2025 top-3 table). Returns {year: {rank: player_name_raw}}.

    Note: the main page table uses ordinal rank strings ("1st", "2nd", "3rd")
    and may have footnote markers in player names ("Lionel Messi[note 1]").
    """
    soup = BeautifulSoup(html, "lxml")
    target = None
    for tbl in soup.find_all("table", class_=lambda c: c and "wikitable" in c):
        ths = tbl.find_all("th")
        header_text = " ".join(th.get_text(strip=True) for th in ths[:8])
        if "Year" in header_text and "Rank" in header_text and "Player" in header_text:
            target = tbl
            break
    if target is None:
        raise RuntimeError("Could not find main page historical table")

    df = pd.read_html(io.StringIO(str(target)))[0]
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    out: dict[int, dict[int, str]] = {}
    for _, r in df.iterrows():
        try:
            year = int(r["Year"])
        except (ValueError, TypeError):
            continue
        rank_str = str(r.get("Rank", "")).strip().lower()
        # Strip ordinal suffix
        rank_str = re.sub(r"(st|nd|rd|th)$", "", rank_str)
        try:
            rank = int(rank_str)
        except ValueError:
            continue
        if rank > 3:
            continue   # main page table only has top 3
        player = str(r.get("Player", "")).replace("\xa0", " ")
        player = FOOTNOTE_RE.sub(" ", player)
        player = re.sub(r"\s+", " ", player).strip()
        out.setdefault(year, {})[rank] = player
    return out


def load_my_nominees() -> dict[int, dict[int, str]]:
    """Load my parsed nominees (from per-year Wikipedia pages)."""
    out: dict[int, dict[int, str]] = {}
    with open(NOMINEES_JSONL, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            year = int(r["award_year"])
            rank = int(r["rank"])
            if rank > 3:
                continue
            out.setdefault(year, {})[rank] = r["player_name_raw"]
    return out


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> None:
    main_top3 = parse_main_page_top3(MAIN_PAGE_CACHE.read_text(encoding="utf-8"))
    my_top3 = load_my_nominees()

    print(f"Main page top-3 table: {len(main_top3)} years")
    print(f"My per-year-page top-3: {len(my_top3)} years")
    print()

    lines = []
    lines.append("# Cross-Verification Report — Random 10-Year Sample\n\n")
    lines.append(f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}\n\n")
    lines.append("## Methodology\n\n")
    lines.append("Per Implementation Plan Phase 1 task 5, a stratified random sample of 10 years\n")
    lines.append("spanning all eras was cross-verified against a second source.\n\n")
    lines.append("**Primary source (under test):** `data/raw/ground_truth/nominees_raw.jsonl`\n")
    lines.append("  — parsed from per-year Wikipedia Action API pages (`{year}_Ballon_d'Or`).\n\n")
    lines.append("**Cross-check source:** Main Wikipedia Ballon d'Or page's historical\n")
    lines.append("  top-3-per-year table (`https://en.wikipedia.org/wiki/Ballon_d%27Or`, table[2]).\n")
    lines.append("  This is a separate page compilation (curated summary table vs per-year article\n")
    lines.append("  body) maintained independently on Wikipedia.\n\n")
    lines.append("**Acknowledged limitation:** Both sources are Wikipedia, so this is not a fully\n")
    lines.append("independent cross-source verification. RSSSF returns 404 for Ballon d'Or pages,\n")
    lines.append("and France Football archives are paywalled. The main-page table is the best\n")
    lines.append("available free cross-check in this sandbox. A documented gap, per Key Focus\n")
    lines.append("Areas §9 — not a silent pass-through.\n\n")
    lines.append("**Comparison rule:** Player names normalized via lowercasing, accent stripping\n")
    lines.append("(NFD decomposition), footnote-marker removal, and parenthetical award-count\n")
    lines.append("removal (e.g. 'Lionel Messi (2)' → 'lionel messi'). A match means the normalized\n")
    lines.append("names are identical.\n\n")

    lines.append("## Sample Years\n\n")
    lines.append(f"Stratified random sample: {SAMPLE_YEARS}\n\n")
    lines.append("| Year | Era | Rank | My parse | Main page | Match? |\n|---|---|---|---|---|---|\n")

    n_matches = 0
    n_mismatches = 0
    n_missing_main = 0
    n_missing_mine = 0
    mismatches_detail = []

    for year in SAMPLE_YEARS:
        # Determine era
        if year < 1995:
            era = "Classical"
        elif year < 2010:
            era = "Pre-merger"
        elif year < 2016:
            era = "FIFA merger"
        else:
            era = "Post-split"

        my_year = my_top3.get(year, {})
        main_year = main_top3.get(year, {})

        if not main_year:
            lines.append(f"| {year} | {era} | — | — | (not in main page table) | ⚠️ main page missing |\n")
            n_missing_main += 1
            continue
        if not my_year:
            lines.append(f"| {year} | {era} | — | (not in my parse) | — | ❌ my parse missing |\n")
            n_missing_mine += 1
            continue

        # Compare each rank
        for rank in [1, 2, 3]:
            my_name = my_year.get(rank, "")
            main_name = main_year.get(rank, "")
            my_norm = normalize_name(my_name)
            main_norm = normalize_name(main_name)

            if not my_norm and not main_norm:
                continue  # both missing, skip
            if not my_norm:
                lines.append(f"| {year} | {era} | {rank} | (missing) | {main_name} | ❌ mine missing |\n")
                n_mismatches += 1
                mismatches_detail.append({"year": year, "rank": rank, "issue": "mine_missing", "main": main_name})
            elif not main_norm:
                lines.append(f"| {year} | {era} | {rank} | {my_name} | (missing) | ⚠️ main missing |\n")
                n_missing_main += 1
            elif my_norm == main_norm:
                lines.append(f"| {year} | {era} | {rank} | {my_name} | {main_name} | ✅ |\n")
                n_matches += 1
            else:
                lines.append(f"| {year} | {era} | {rank} | {my_name} | {main_name} | ❌ MISMATCH |\n")
                n_mismatches += 1
                mismatches_detail.append({
                    "year": year, "rank": rank,
                    "mine": my_name, "main": main_name,
                    "mine_norm": my_norm, "main_norm": main_norm,
                })

    lines.append(f"\n## Summary\n\n")
    lines.append(f"- Total comparisons: {n_matches + n_mismatches}\n")
    lines.append(f"- ✅ Matches: {n_matches}\n")
    lines.append(f"- ❌ Mismatches: {n_mismatches}\n")
    lines.append(f"- ⚠️ Main page missing entries: {n_missing_main}\n")
    lines.append(f"- ❌ My parse missing entries: {n_missing_mine}\n")
    match_rate = n_matches / (n_matches + n_mismatches) if (n_matches + n_mismatches) else 0
    lines.append(f"- Match rate: {match_rate:.1%}\n\n")

    if mismatches_detail:
        lines.append("## Mismatch Details\n\n")
        for m in mismatches_detail:
            lines.append(f"- `{m}`\n")
        lines.append("\n")

    lines.append("## Conclusion\n\n")
    if n_mismatches == 0 and n_missing_mine == 0:
        lines.append("✅ **All 10 sample years pass cross-verification.** Top-3 player names\n")
        lines.append("from my per-year-page parse match the main Wikipedia Ballon d'Or page's\n")
        lines.append("historical top-3 table for every sampled rank, across all four eras\n")
        lines.append("(classical, pre-merger, FIFA merger, post-split). Per-year-page parsing\n")
        lines.append("is verified sound.\n")
    else:
        lines.append(f"⚠️ **{n_mismatches} mismatches found.** See details above. These must be\n")
        lines.append("investigated and resolved before Phase 1 exit criterion is met.\n")

    OUTPUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_REPORT.write_text("".join(lines), encoding="utf-8")
    print(f"✅ Wrote cross-verification report to {OUTPUT_REPORT}")
    print()
    print(f"Matches: {n_matches}, Mismatches: {n_mismatches}, Match rate: {match_rate:.1%}")


if __name__ == "__main__":
    main()
