"""Review point B1: does the backfill label (Algolia points today) give other domain hit
rates than the poller's 24-hour label? Compares both on the same stories (14.-28.10).

    gcloud storage rsync -r gs://$BUCKET/v1/snapshots data/snapshots   (and labels)
    uv run scripts/compare_labels.py
"""

from datetime import UTC, date, datetime

import httpx

from hn_breakout import storage
from hn_breakout.features import HIT_THRESHOLD, domain_of

lo = int(datetime(2026, 10, 14, tzinfo=UTC).timestamp())
hi = int(datetime(2026, 10, 29, tzinfo=UTC).timestamp())

snaps = storage.read("snapshots", date(2026, 10, 14), date(2026, 10, 29))
snaps = snaps.drop_duplicates("story_id")
snaps = snaps[snaps["time"].between(lo, hi - 1)]
labels = storage.read("labels", date(2026, 10, 14), date(2026, 10, 30)).drop_duplicates("story_id")
df = snaps[["story_id", "url"]].merge(labels[["story_id", "final_score"]], on="story_id")
df["domain"] = df["url"].map(domain_of)
df["hit_24h"] = df["final_score"] >= HIT_THRESHOLD

# all stories of the window with >= 100 points on Algolia today: fewer than 1000, one request
resp = httpx.get(
    "https://hn.algolia.com/api/v1/search_by_date",
    params={
        "tags": "story",
        "hitsPerPage": 1000,
        "numericFilters": f"created_at_i>={lo},created_at_i<{hi},points>={HIT_THRESHOLD}",
        "attributesToRetrieve": "points",
        "attributesToHighlight": "",
    },
    timeout=60,
)
resp.raise_for_status()
data = resp.json()
if data["nbHits"] > len(data["hits"]):
    raise SystemExit("Algolia returned only part of the hits, use a shorter window")
df["hit_algolia"] = df["story_id"].isin({int(h["objectID"]) for h in data["hits"]})

print(
    f"{len(df)} stories: {df['hit_24h'].mean():.2%} hits with the 24 h label, "
    f"{df['hit_algolia'].mean():.2%} with Algolia points"
)

per = df.groupby("domain").agg(
    stories=("story_id", "size"), hit_24h=("hit_24h", "mean"), hit_algolia=("hit_algolia", "mean")
)
per = per[per["stories"] >= 20]  # smaller domains say nothing on their own
per["diff"] = per["hit_algolia"] - per["hit_24h"]
print(
    f"{len(per)} domains with >= 20 stories: Algolia higher in {(per['diff'] > 0).sum()}, "
    f"equal in {(per['diff'] == 0).sum()}, lower in {(per['diff'] < 0).sum()}; "
    f"mean difference {per['diff'].mean():+.1%}"
)
for col in ("hit_24h", "hit_algolia", "diff"):
    per[col] = (100 * per[col]).round(1)
print(per.sort_values("diff", ascending=False).head(10).to_string())
