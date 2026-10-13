"""Feature definitions, used by training and inference alike (one place = no training-serving skew).

story_features() turns raw snapshots into one row per story, using only what was known
when the story was 30 minutes old. main() rebuilds the daily feature table (run once a day).
domain_hit_rate is left to training/inference: it may only use labels known at prediction time.
"""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pandas as pd

from hn_breakout import storage

log = logging.getLogger(__name__)

PREDICTION_AGE = 30 * 60
CHECKPOINTS = (10, 20, 30)  # minutes
MAX_STALENESS = 7 * 60  # "points at 10 min" may come from a snapshot taken at 3-10 min, not earlier
LATE_FIRST_SEEN = 12 * 60  # first seen this late = we missed its start (e.g. second-chance pool)
HIT_THRESHOLD = 100


def domain_of(url) -> str:
    if not isinstance(url, str) or not url:
        return "self"  # Ask HN and other text posts
    return urlparse(url).netloc.lower().removeprefix("www.") or "self"


def value_at(snaps: pd.DataFrame, age: int, col: str):
    """Value from the last snapshot at or before `age`, or None if that snapshot is too old."""
    before = snaps[snaps["age_s"] <= age]
    if before.empty or age - before["age_s"].iloc[-1] > MAX_STALENESS:
        return None
    return before[col].iloc[-1]


def story_features(snapshots: pd.DataFrame) -> pd.DataFrame:
    snaps = snapshots[snapshots["age_s"] <= PREDICTION_AGE].sort_values(["story_id", "age_s"])
    if snaps.empty:
        return pd.DataFrame()
    rows = []
    for story_id, s in snaps.groupby("story_id"):
        rows.append(
            {
                "story_id": story_id,
                "time": s["time"].iloc[-1],
                **{f"points_{m}": value_at(s, m * 60, "score") for m in CHECKPOINTS},
                "comments_30": value_at(s, PREDICTION_AGE, "descendants"),
                "karma": s["karma"].dropna().iloc[-1] if s["karma"].notna().any() else None,
                "user_created": s["user_created"].dropna().iloc[-1]
                if s["user_created"].notna().any()
                else None,
                "title": s["title"].iloc[-1],
                "url": s["url"].iloc[-1],
                "first_seen_age_s": s["age_s"].iloc[0],
            }
        )
    df = pd.DataFrame(rows)
    df["title"] = df["title"].fillna("")

    df["gain_20_30"] = df["points_30"] - df["points_20"]
    df["comments_per_point"] = df["comments_30"] / df["points_30"].clip(lower=1)
    posted = pd.to_datetime(df["time"], unit="s", utc=True)
    df["hour_utc"] = posted.dt.hour
    df["weekday"] = posted.dt.weekday
    df["n_concurrent"] = [df["time"].between(t + 1, t + PREDICTION_AGE).sum() for t in df["time"]]
    df["is_show_hn"] = df["title"].str.startswith("Show HN").astype(int)
    df["is_ask_hn"] = df["title"].str.startswith("Ask HN").astype(int)
    df["has_url"] = df["url"].notna().astype(int)
    df["domain"] = df["url"].map(domain_of)
    df["title_len"] = df["title"].str.len()
    df["has_number"] = df["title"].str.contains(r"\d").astype(int)
    df["has_question_mark"] = df["title"].str.contains("?", regex=False).astype(int)
    df["account_age_days"] = (df["time"] - df["user_created"]) / 86400
    # quality flags: incomplete or re-surfaced stories are left out of training
    df["complete"] = df[[f"points_{m}" for m in CHECKPOINTS]].notna().all(axis=1).astype(int)
    df["late_first_seen"] = (df["first_seen_age_s"] > LATE_FIRST_SEEN).astype(int)
    return df


def attach_labels(features: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Add the target; stories without a label yet are dropped."""
    out = features.merge(
        labels.drop_duplicates("story_id")[["story_id", "final_score"]], on="story_id"
    )
    out["hit"] = (out["final_score"] >= HIT_THRESHOLD).astype(int)
    return out


def build_day(day, root: str = storage.ROOT) -> pd.DataFrame:
    # a story posted at 23:50 has snapshots after midnight and its label two days later
    snaps = storage.read("snapshots", day, day + timedelta(days=1), root)
    labels = storage.read("labels", day, day + timedelta(days=2), root)
    if snaps.empty or labels.empty:
        return pd.DataFrame()
    feats = story_features(snaps)
    feats = feats[pd.to_datetime(feats["time"], unit="s", utc=True).dt.date == day]
    return attach_labels(feats, labels)


def main() -> None:
    """Rebuild the last 3 days; re-running overwrites the same file, so it is safe."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = datetime.now(UTC).date()
    for back in (1, 2, 3):
        day = today - timedelta(days=back)
        df = build_day(day)
        midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
        storage.write("features", df, midnight)  # always features/date=<day>/000000.parquet
        log.info("%s: %d stories, %d hits", day, len(df), int(df["hit"].sum()) if len(df) else 0)
