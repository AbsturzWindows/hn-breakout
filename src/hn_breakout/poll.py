"""The poller. Runs every 5 minutes; each run writes four tables:

snapshots  every story younger than 35 min (karma only near the 30-min prediction time)
frontpage  the current top 30, to check how well "100 points" matches the real front page
labels     final score of stories that are now 24 h old (up to 2 h late, so missed runs catch up)
runs       one row with counts, errors and duration, for monitoring

HN keeps no score history, so a missed snapshot is lost for good: each step is isolated
and a failed item is skipped instead of crashing the run.
"""

import logging
import sys
import time
from datetime import UTC, datetime

import httpx
import pandas as pd

from hn_breakout import storage

log = logging.getLogger(__name__)

API = "https://hacker-news.firebaseio.com/v0"
SNAPSHOT_MAX_AGE = 35 * 60
KARMA_MIN_AGE = 25 * 60
LABEL_AGE = 24 * 3600
CATCH_UP = 2 * 3600
FRONTPAGE_SIZE = 30


def get(http: httpx.Client, path: str, retries: int = 3):
    """GET {API}/{path}.json, retrying network errors and bad statuses. None if all attempts fail."""
    for attempt in range(retries):
        try:
            resp = http.get(f"{API}/{path}.json")
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # ValueError: response is not valid JSON
            log.warning("%s failed (attempt %d): %s", path, attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(2**attempt)
    return None


def get_ids(http: httpx.Client, name: str) -> list[int]:
    ids = get(http, name)
    if ids is None:
        raise RuntimeError(f"{name} unavailable")
    return ids


def is_story(item: dict | None) -> bool:
    return (
        bool(item)
        and item.get("type") == "story"
        and not item.get("deleted")
        and not item.get("dead")
    )


def snapshots(http: httpx.Client, now: datetime) -> pd.DataFrame:
    now_s = int(now.timestamp())
    rows = []
    for story_id in get_ids(http, "newstories"):  # newest first
        item = get(http, f"item/{story_id}")
        if not item or "time" not in item:
            continue
        age = now_s - item["time"]
        if age > SNAPSHOT_MAX_AGE:
            break
        if not is_story(item):
            continue
        user = get(http, f"user/{item['by']}") if age >= KARMA_MIN_AGE and item.get("by") else None
        rows.append(
            {
                "polled_at": now,
                "story_id": story_id,
                "time": item["time"],
                "age_s": age,
                "score": item.get("score", 0),
                "descendants": item.get("descendants", 0),
                "title": item.get("title", ""),
                "url": item.get("url"),
                "by": item.get("by"),
                "karma": user.get("karma") if user else None,
                "user_created": user.get("created") if user else None,
            }
        )
    return pd.DataFrame(rows)


def frontpage(http: httpx.Client, now: datetime) -> pd.DataFrame:
    ids = get_ids(http, "topstories")[:FRONTPAGE_SIZE]
    return pd.DataFrame({"polled_at": now, "rank": range(1, len(ids) + 1), "story_id": ids})


def labels(http: httpx.Client, now: datetime, root: str) -> pd.DataFrame:
    newest = int(now.timestamp()) - LABEL_AGE
    oldest = newest - CATCH_UP
    first_day = datetime.fromtimestamp(oldest, UTC).date()
    snaps = storage.read("snapshots", first_day, datetime.fromtimestamp(newest, UTC).date(), root)
    if snaps.empty:
        return snaps
    todo = set(snaps.loc[snaps["time"].between(oldest, newest - 1), "story_id"])
    done = storage.read("labels", first_day, now.date(), root)
    if not done.empty:
        todo -= set(done["story_id"])
    rows = []
    for story_id in sorted(todo):
        item = get(http, f"item/{story_id}")
        if item is None:
            continue  # still inside the catch-up window, so the next run tries again
        rows.append(
            {
                "labeled_at": now,
                "story_id": story_id,
                "final_score": item.get("score", 0),
                "dead": bool(item.get("dead") or item.get("deleted")),
            }
        )
    return pd.DataFrame(rows)


def run(
    now: datetime | None = None, http: httpx.Client | None = None, root: str = storage.ROOT
) -> dict:
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    start = time.monotonic()
    report = {"polled_at": now, "n_snapshots": 0, "n_frontpage": 0, "n_labels": 0}
    errors = []
    with http or httpx.Client(timeout=10) as client:
        steps = {
            "snapshots": lambda: snapshots(client, now),
            "frontpage": lambda: frontpage(client, now),
            "labels": lambda: labels(client, now, root),
        }
        for table, step in steps.items():
            try:
                df = step()
                storage.write(table, df, now, root)
                report[f"n_{table}"] = len(df)
            except Exception as exc:
                log.exception("%s failed", table)
                errors.append(f"{table}: {exc}")
    report["errors"] = "; ".join(errors)
    report["duration_s"] = round(time.monotonic() - start, 2)
    storage.write("runs", pd.DataFrame([report]), now, root)
    log.info("run done: %s", report)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # A non-zero exit marks the Cloud Run execution as failed.
    sys.exit(1 if run()["errors"] else 0)
