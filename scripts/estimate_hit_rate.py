"""Hit rate from Algolia's HN history, the check promised in the proposal:
below 2 % hits at 100 points -> use 50 points instead. Run once: uv run python scripts/estimate_hit_rate.py
"""

from datetime import UTC, datetime, timedelta

import httpx

DAYS = 28
# start 2 days back: younger stories have not reached their final score yet
end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
start = end - timedelta(days=DAYS)


def count(min_points: int = 0) -> int:
    filters = f"created_at_i>={int(start.timestamp())},created_at_i<{int(end.timestamp())},points>={min_points}"
    resp = httpx.get(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={"tags": "story", "numericFilters": filters, "hitsPerPage": 0},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["nbHits"]


stories, hits100, hits50 = count(), count(100), count(50)
per_day = stories / DAYS
print(f"{start:%Y-%m-%d} to {end:%Y-%m-%d}: {per_day:.0f} stories per day")
for name, hits in (("100", hits100), ("50", hits50)):
    rate = hits / stories
    print(f">= {name} points: {rate:.2%}, about {rate * per_day * 14:.0f} hits in 2 test weeks")
print("decision:", "keep 100 points" if hits100 / stories >= 0.02 else "switch to 50 points")
