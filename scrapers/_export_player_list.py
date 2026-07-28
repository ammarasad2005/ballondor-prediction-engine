"""Export the list of unique player names from ground_truth.parquet
to a text file for the bash+curl fetcher to consume.
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path("/home/z/my-project/ballondor-engine")
GROUND_TRUTH_PARQUET = PROJECT_ROOT / "data" / "processed" / "ground_truth.parquet"
OUTPUT_TXT = PROJECT_ROOT / "data" / "raw" / "stats" / "_player_list.txt"


def slugify(name: str) -> str:
    import re
    if not name:
        return ""
    slug = re.sub(r"\s+", "_", name.strip())
    slug = re.sub(r"\s*\[[^\]]*\]\s*", "", slug)
    return slug


def main() -> None:
    df = pd.read_parquet(GROUND_TRUTH_PARQUET)
    unique_players = sorted(df["player_name_raw"].unique().tolist())
    print(f"Total unique players in ground truth: {len(unique_players)}")

    OUTPUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        for name in unique_players:
            slug = slugify(name)
            f.write(f"{name}\t{slug}\n")
    print(f"Wrote {OUTPUT_TXT} ({len(unique_players)} entries)")


if __name__ == "__main__":
    main()
