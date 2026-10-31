"""Backfill past stories from Algolia's HN search into the `history` table.

HN keeps no score history, so this gives no momentum features. It gives the outcome of
past stories (title, url, author, final points), so domain_hit_rate has history from the
first training day. Points are as of the backfill, not at 24 h; they barely change later.

    hn-backfill 2026-07-15 2026-10-12
"""

import logging
import sys
from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd

from hn_breakout import storage

log = logging.getLogger(__name__)

API = "https://hn.algolia.com/api/v1/search_by_date"


def stories(http: httpx.Client, start: int, end: int) -> list[dict]:
    """All stories created in [start, end), in unix seconds."""
    resp = http.get(
        API,
        params={
            "tags": "story",
            "hitsPerPage": 1000,
            "numericFilters": f"created_at_i>={start},created_at_i<{end}",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if data["nbHits"] > len(data["hits"]):  # never silently keep half an hour
        raise RuntimeError(f"Algolia returned {len(data['hits'])} of {data['nbHits']} stories")
    return data["hits"]


def backfill_day(http: httpx.Client, day: date) -> pd.DataFrame:
    # one request per hour: Algolia returns at most 1000 stories per request, a day can have more
    midnight = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    rows = []
    for hour in range(24):
        for hit in stories(http, midnight + hour * 3600, midnight + (hour + 1) * 3600):
            rows.append(
                {
                    "story_id": int(hit["objectID"]),
                    "time": hit["created_at_i"],
                    "title": hit.get("title") or "",
                    "url": hit.get("url"),
                    "by": hit.get("author"),
                    "final_score": hit.get("points") or 0,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) != 3:
        sys.exit("usage: hn-backfill START END, dates as YYYY-MM-DD")
    start, end = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
    if end > datetime.now(UTC).date() - timedelta(days=2):
        sys.exit("END must be at least 2 days ago: younger stories have no final score yet")
    with httpx.Client(timeout=30) as http:
        for i in range((end - start).days + 1):
            day = start + timedelta(days=i)
            df = backfill_day(http, day)
            midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
            storage.write("history", df, midnight)  # history/date=<day>/000000.parquet
            log.info("%s: %d stories", day, len(df))
