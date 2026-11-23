"""Daily completeness of the poller data and the feature build (review points E2, E1).
Runs daily in GitHub Actions (.github/workflows/completeness.yml), reading the bucket.
Locally, on a copy: gcloud storage rsync -r gs://$BUCKET/v1 data

    uv run scripts/completeness.py [first-day, default 7 days ago]
"""

import sys
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from hn_breakout import storage
from hn_breakout.features import attach_labels, story_features

today = datetime.now(UTC).date()
start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else today - timedelta(days=7)

runs = storage.read("runs", start, today)
if runs.empty:
    sys.exit(f"no runs since {start} in {storage.ROOT}")
runs = runs.sort_values("polled_at")
runs["gap"] = runs["polled_at"].diff() > pd.Timedelta(minutes=7)
runs["failed"] = runs["errors"] != ""
r = runs.groupby(runs["polled_at"].dt.date)
report = pd.DataFrame(
    {
        "runs": r.size(),
        "failed": r["failed"].sum(),
        "gaps": r["gap"].sum(),
        "max_s": r["duration_s"].max(),
    }
)

# only stories whose whole first 35 minutes fall inside the poller's uptime
first, last = runs["polled_at"].min().timestamp(), runs["polled_at"].max().timestamp()
feats = story_features(storage.read("snapshots", start, today))
if not feats.empty:
    feats = feats[feats["time"].between(first, last - 35 * 60)]
    s = feats.groupby(pd.to_datetime(feats["time"], unit="s", utc=True).dt.date)
    report["stories"] = s.size()
    report["complete_%"] = 100 * s["complete"].mean()
    report["karma_%"] = 100 * s["karma"].agg(lambda k: k.notna().mean())
    report["late_%"] = 100 * s["late_first_seen"].mean()

    labels = storage.read("labels", start, today)
    if not labels.empty:
        labelled = attach_labels(feats, labels)
        lab = labelled.groupby(pd.to_datetime(labelled["time"], unit="s", utc=True).dt.date)
        report["labels"] = lab.size()
        report["hit_%"] = 100 * lab["hit"].mean()
        print("duplicate labels:", labels["story_id"].duplicated().sum())

built = storage.read("features", start, today)
if not built.empty:  # did the daily feature build run, and with which code?
    b = built.groupby(pd.to_datetime(built["time"], unit="s", utc=True).dt.date)
    report["feature_rows"] = b.size()
    if "code_version" in built:
        report["feature_build"] = b["code_version"].first()

print(report.round(1).to_string())
print("A full day has 288 runs. Labels arrive 24 h after posting, so the last day is incomplete.")
