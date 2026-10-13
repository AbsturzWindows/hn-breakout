# HN Breakout

Early prediction of Hacker News front-page stories (HSLU MLOps HS26).

**Prediction:** 30 minutes after a story is posted, will it reach **≥ 100 points within 24 hours**?
The model learns from each story's votes in its first 30 minutes, recorded every 5 minutes by a
poller, because HN itself keeps no score history. Proposal: [docs/proposal.pdf](docs/proposal.pdf)

## Pipelines

| Pipeline | Code | Runs on | Status |
|---|---|---|---|
| Feature | `poll.py` every 5 min, `features.py` daily | Cloud Run Jobs + Cloud Scheduler | MS2 |
| Training | reads `features`, trains LightGBM, registers in MLflow | GitHub Actions | MS3 |
| Inference | scores stories at 30 min, Streamlit UI | Cloud Run | MS4 |

Data is stored as Parquet in `{STORAGE_ROOT}/{table}/date=YYYY-MM-DD/`, locally or on Google Cloud Storage:
`snapshots`, `frontpage`, `labels`, `runs` (from the poller) and `features` (one row per labelled story).

```
src/hn_breakout/
  storage.py    read/write the Parquet tables
  poll.py       the poller (HN API -> snapshots, front page, labels, run log)
  features.py   feature definitions shared by training and inference + daily feature build
scripts/        one-off: hit-rate estimate from Algolia
tests/          unit tests against a fake HN API
docs/           proposal, milestone summaries, deployment guide
```

## Run locally

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run hn-poll              # one poller run, writes to ./data
uv run hn-build-features    # feature tables for the last 3 days
uv run pytest
```

With Docker: `docker build -t hn-breakout . && docker run --rm -v "$PWD/data:/app/data" hn-breakout`

## Deploy

See [docs/deploy_gcp.md](docs/deploy_gcp.md).
