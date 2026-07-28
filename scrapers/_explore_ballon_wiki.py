"""Inspect every table on the Wikipedia Ballon d'Or page to identify
which tables hold which eras of winner/nominee data.

This is a one-shot exploration script — its output directs the design of
the actual ground_truth_scraper.py in the next step.
"""
from __future__ import annotations
import io
import os
import requests
import pandas as pd
from bs4 import BeautifulSoup

URL = "https://en.wikipedia.org/wiki/Ballon_d%27Or"
HEADERS = {"User-Agent": "BallonDorPredictBot/0.1 (research; contact: agent@local)"}

CACHE = "/home/z/my-project/ballondor-engine/data/raw/_phase1_ballon_wiki.html"


def fetch_page() -> str:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    if os.path.exists(CACHE) and os.path.getsize(CACHE) > 1_000_000:
        with open(CACHE, "r", encoding="utf-8") as f:
            return f.read()
    r = requests.get(URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    with open(CACHE, "w", encoding="utf-8") as f:
        f.write(r.text)
    return r.text


def main() -> None:
    html = fetch_page()
    soup = BeautifulSoup(html, "lxml")

    # Locate every <table> on the page, with its preceding heading for context.
    tables = soup.find_all("table", class_=lambda c: c and "wikitable" in c)
    print(f"Found {len(tables)} wikitables\n")

    for i, tbl in enumerate(tables):
        # Look back for the nearest preceding heading
        prev = tbl
        heading_text = "(no heading)"
        for _ in range(50):
            prev = prev.find_previous(["h2", "h3", "h4"])
            if prev is None:
                break
            txt = prev.get_text(strip=True)
            if txt:
                heading_text = txt
                break

        try:
            df = pd.read_html(str(tbl))[0]
            shape = df.shape
            cols = list(df.columns)[:6]
            print(f"[{i:02d}] heading={heading_text!r}  shape={shape}  cols={cols}")
            # Peek at first 2 rows
            for ridx, row in df.head(2).iterrows():
                vals = [str(v)[:30] for v in row.tolist()[:5]]
                print(f"     row{ridx}: {vals}")
        except Exception as e:
            print(f"[{i:02d}] heading={heading_text!r}  PARSE ERROR: {e}")
        print()


if __name__ == "__main__":
    main()
