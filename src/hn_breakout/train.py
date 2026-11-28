"""Training pipeline: train three LightGBM models (momentum only, full, full + title words),
compare them with the momentum baseline (points at 30 min) and log everything to MLflow.

    hn-train --validate   the fixed periods from docs/decisions.md (2026-11-22), validation only
    hn-train --final      also evaluate once on the test period and register the best model
    hn-train --retrain    weekly: train on everything but the last 7 complete days, validate on
                          those, register the best model

Nothing is tuned.
"""

import os
import sys
from datetime import UTC, date, datetime, timedelta

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import average_precision_score

from hn_breakout import storage
from hn_breakout.features import domain_hit_rate

TRAIN = (date(2026, 10, 13), date(2026, 11, 8))
VALID = (date(2026, 11, 9), date(2026, 11, 15))
TEST = (date(2026, 11, 16), date(2026, 11, 29))

MOMENTUM = [
    "points_10",
    "points_20",
    "points_30",
    "gain_20_30",
    "comments_30",
    "comments_per_point",
]
FULL = MOMENTUM + [
    "hour_utc", "weekday", "n_concurrent", "is_show_hn", "is_ask_hn", "has_url", "title_len",
    "has_number", "has_question_mark", "karma", "account_age_days", "domain_hit_rate",
]  # fmt: skip
PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "is_unbalance": True,
    "seed": 0,
    "verbosity": -1,
}
ROUNDS = 300


def precision_at_10(df: pd.DataFrame, score) -> float:
    """The daily top 10 (docs/decisions.md, B2): share of hits among each day's 10 highest
    scores, averaged over the days."""
    top = df.assign(score=score).sort_values("score", ascending=False).groupby("day").head(10)
    return top.groupby("day")["hit"].mean().mean()


def bootstrap(y, model, baseline, n=1000) -> tuple[float, float]:
    """95 % interval of PR-AUC(model) - PR-AUC(baseline), resampling stories."""
    rng = np.random.default_rng(0)
    y, model, baseline = np.asarray(y), np.asarray(model), np.asarray(baseline)
    diffs = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        diffs.append(
            average_precision_score(y[i], model[i]) - average_precision_score(y[i], baseline[i])
        )
    return tuple(np.percentile(diffs, [2.5, 97.5]))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--validate"
    if mode == "--validate":
        train_days, valid_days, test_days = TRAIN, VALID, None
    elif mode == "--final":
        train_days, valid_days, test_days = TRAIN, VALID, TEST
    elif mode == "--retrain":
        last = datetime.now(UTC).date() - timedelta(days=3)  # newest day with all labels
        if last <= TEST[1]:  # the test period stays untouched until the final evaluation
            sys.exit("the weekly retrain starts after the test period (from 03.12.)")
        train_days = (TRAIN[0], last - timedelta(days=7))
        valid_days = (last - timedelta(days=6), last)
        test_days = None
    else:
        sys.exit("usage: hn-train [--validate | --final | --retrain]")

    df = storage.read("features", train_days[0], (test_days or valid_days)[1])
    df["domain_hit_rate"] = domain_hit_rate(df, df)  # as of each story's prediction time (A1)
    df = df[(df["complete"] == 1) & (df["late_first_seen"] == 0)]  # incomplete or seen late
    df["day"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.date
    train = df[df["day"].between(*train_days)]
    valid = df[df["day"].between(*valid_days)]
    test = df[df["day"].between(*test_days)] if test_days else None
    if test_days and test["day"].nunique() < 14:
        sys.exit("the test period is not complete yet (labels arrive 24 h after posting)")

    momentum = lgb.train(PARAMS, lgb.Dataset(train[MOMENTUM], train["hit"]), ROUNDS)
    full = lgb.train(PARAMS, lgb.Dataset(train[FULL], train["hit"]), ROUNDS)
    # title words, learned from the training period only (rule from the MS1 answer to A1);
    # a word must appear in at least 20 training stories
    words = TfidfVectorizer(min_df=20, stop_words="english").fit(train["title"])

    def with_words(part):
        return hstack([part[FULL].to_numpy(float), words.transform(part["title"])], format="csr")

    title = lgb.train(PARAMS, lgb.Dataset(with_words(train), train["hit"]), ROUNDS)

    def scores(part):
        return {
            "baseline": part["points_30"],
            "momentum": momentum.predict(part[MOMENTUM]),
            "full": full.predict(part[FULL]),
            "title": title.predict(with_words(part)),
        }

    metrics = {}
    for name, part in [("valid", valid)] + ([("test", test)] if test_days else []):
        print(f"{name}: {len(part)} stories, {part['hit'].sum()} hits")
        s = scores(part)
        for model, score in s.items():
            ap = average_precision_score(part["hit"], score)
            p10 = precision_at_10(part, score)
            metrics[f"{name}_ap_{model}"], metrics[f"{name}_p10_{model}"] = ap, p10
            print(f"  {model:9} PR-AUC {ap:.3f}   precision@10 per day {p10:.3f}")
        for model in ("momentum", "full", "title"):
            lo, hi = bootstrap(part["hit"], s[model], s["baseline"])
            metrics[f"{name}_{model}_minus_baseline_lo"] = lo
            metrics[f"{name}_{model}_minus_baseline_hi"] = hi
            print(f"  {model} - baseline PR-AUC, 95 % interval: {lo:+.3f} to {hi:+.3f}")
    # the model to register is chosen on the validation week, never on the test period
    best = max(("momentum", "full", "title"), key=lambda model: metrics[f"valid_ap_{model}"])

    mlflow.set_experiment("hn-breakout")
    with mlflow.start_run():
        mlflow.set_tags(
            {
                "code_version": os.environ.get("GIT_SHA", "local"),  # the training code
                "feature_builds": ",".join(df["code_version"].unique()),  # the feature code (A3)
                "best_on_valid": best,
            }
        )
        mlflow.log_params(
            {
                "mode": mode,
                "train": f"{train_days[0]} to {train_days[1]}",
                "valid": f"{valid_days[0]} to {valid_days[1]}",
                "test": f"{test_days[0]} to {test_days[1]}" if test_days else "none",
            }
        )
        mlflow.log_params({"rounds": ROUNDS, **PARAMS})
        mlflow.log_params({"train_stories": len(train), "train_hits": int(train["hit"].sum())})
        mlflow.log_metrics(metrics)
        for model_name, model in (("momentum", momentum), ("full", full), ("title", title)):
            register = "hn-breakout" if mode != "--validate" and model_name == best else None
            mlflow.lightgbm.log_model(model, name=model_name, registered_model_name=register)
        mlflow.sklearn.log_model(
            words, name="title_words", serialization_format="pickle"
        )  # inference needs the same words
