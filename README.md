# HN Breakout

Early prediction of Hacker News front-page stories (HSLU MLOps HS26).

**Prediction:** 30 minutes after a story is posted, will it reach **≥ 100 points within 24 hours**?
The model learns from each story's votes in its first 30 minutes, recorded every 5 minutes by a
poller, because HN itself keeps no score history. Proposal: [docs/proposal.pdf](docs/proposal.pdf),
milestone summaries in [docs/](docs/).

**Label:** the story's score at the first successful poller run at least 24 hours after it was
posted (usually 24h00 to 24h05). If no run succeeds between 24 and 26 hours, the story gets no
label and is left out. Hit = score ≥ 100 ([docs/decisions.md](docs/decisions.md)).

**What is different from earlier projects:** the KTH ID2223 2023/24 project
[group-5-id2223/project](https://github.com/group-5-id2223/project) predicts a story's score from
its title (BERT embedding), domain and posting time. Its daily pipeline stores the current top ten
stories and its backfill samples random old items, so each story is saved once, with whatever
score it has when fetched. HN Breakout predicts 30 minutes after posting whether a story reaches
≥ 100 points within 24 hours, from its own vote trajectory recorded every 5 minutes, with the label
always taken at the same age, and it has to beat a momentum baseline.

## Pipelines

| Pipeline | Code | Runs on | Status |
|---|---|---|---|
| Feature | `poll.py` every 5 min, `features.py` daily | Cloud Run Jobs + Cloud Scheduler | MS2 |
| Training | reads `features`, trains LightGBM, registers in MLflow | GitHub Actions | MS3 |
| Inference | scores stories at 30 min, Streamlit UI | Cloud Run | MS4 |

Data is stored as Parquet in `{STORAGE_ROOT}/{table}/date=YYYY-MM-DD/`, locally or on Google Cloud Storage:
`snapshots`, `frontpage`, `labels`, `runs` (from the poller), `features` (one row per labelled story)
and `history` (past stories from Algolia, for `domain_hit_rate`).

```
src/hn_breakout/
  storage.py    read/write the Parquet tables
  poll.py       the poller (HN API -> snapshots, front page, labels, run log)
  features.py   feature definitions shared by training and inference + daily feature build
  backfill.py   past stories from Algolia -> history table
scripts/        hit-rate estimate from Algolia, daily completeness report of the poller data
tests/          unit tests against a fake HN API and Algolia
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

Backfill (both are safe to re-run; `hn-backfill` skips days it already has, so a stopped run resumes):

```bash
uv run hn-build-features 2026-10-13 2026-10-28   # rebuild features for a date range
uv run hn-backfill 2026-07-15 2026-10-12         # past stories from Algolia
```

The momentum features themselves cannot be backfilled: HN keeps no score history, so they exist
only from the day the poller started (13.10.2026). Data check: `uv run scripts/completeness.py`.

With Docker: `docker build -t hn-breakout . && docker run --rm -v "$PWD/data:/app/data" hn-breakout`

## Deploy

The schedules (poller every 5 min, features daily at 03:15 UTC) run in Cloud Scheduler, not GitHub Actions.

See [docs/deploy_gcp.md](docs/deploy_gcp.md).
