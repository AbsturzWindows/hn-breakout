"""Parquet files partitioned by day: {root}/{table}/date=YYYY-MM-DD/HHMMSS.parquet

root is a local folder (default ./data) or a bucket like gs://my-bucket/v1.
"""

import os
from datetime import date, datetime, timedelta

import fsspec
import pandas as pd

ROOT = os.environ.get("STORAGE_ROOT", "./data")


def write(table: str, df: pd.DataFrame, ts: datetime, root: str = ROOT) -> None:
    if df.empty:
        return
    fs, base = fsspec.core.url_to_fs(root)
    folder = f"{base}/{table}/date={ts:%Y-%m-%d}"
    if "file" in fs.protocol:  # local disk needs the folder; buckets have no real folders
        fs.makedirs(folder, exist_ok=True)
    with fs.open(f"{folder}/{ts:%H%M%S}.parquet", "wb") as f:
        df.to_parquet(f, index=False)


def read(table: str, start: date, end: date, root: str = ROOT) -> pd.DataFrame:
    """All rows of a table from start to end (inclusive). Empty frame if there are none."""
    fs, base = fsspec.core.url_to_fs(root)
    frames = []
    for i in range((end - start).days + 1):
        for path in sorted(fs.glob(f"{base}/{table}/date={start + timedelta(i)}/*.parquet")):
            with fs.open(path, "rb") as f:
                frames.append(pd.read_parquet(f))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
