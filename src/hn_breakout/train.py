"""Training pipeline: train a momentum-only and a full LightGBM model, compare both with the
momentum baseline (points at 30 min) and log everything to MLflow.

    hn-train            evaluate on the validation week
    hn-train --final    also evaluate once on the test period and register the better model

The periods are fixed in docs/decisions.md (2026-11-22). Nothing is tuned.
"""

import os
import sys
from datetime import date

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
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


def bootstrap(y, full, baseline, n=1000) -> tuple[float, float]:
    """95 % interval of PR-AUC(full) - PR-AUC(baseline), resampling stories."""
    rng = np.random.default_rng(0)
    y, full, baseline = np.asarray(y), np.asarray(full), np.asarray(baseline)
    diffs = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        diffs.append(
            average_precision_score(y[i], full[i]) - average_precision_score(y[i], baseline[i])
        )
    return tuple(np.percentile(diffs, [2.5, 97.5]))


def main() -> None:
    final = "--final" in sys.argv[1:]

    df = storage.read("features", TRAIN[0], TEST[1])
    df["domain_hit_rate"] = domain_hit_rate(df, df)  # as of each story's prediction time (A1)
    df = df[(df["complete"] == 1) & (df["late_first_seen"] == 0)]  # incomplete or seen late
    df["day"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.date
    train = df[df["day"].between(*TRAIN)]
    valid = df[df["day"].between(*VALID)]
    test = df[df["day"].between(*TEST)]
    if final and test["day"].nunique() < 14:
        sys.exit("the test period is not complete yet (labels arrive 24 h after posting)")

    momentum = lgb.train(PARAMS, lgb.Dataset(train[MOMENTUM], train["hit"]), ROUNDS)
    full = lgb.train(PARAMS, lgb.Dataset(train[FULL], train["hit"]), ROUNDS)

    def scores(part):
        return {
            "baseline": part["points_30"],
            "momentum": momentum.predict(part[MOMENTUM]),
            "full": full.predict(part[FULL]),
        }

    metrics = {}
    for name, part in [("valid", valid)] + ([("test", test)] if final else []):
        print(f"{name}: {len(part)} stories, {part['hit'].sum()} hits")
        s = scores(part)
        for model, score in s.items():
            ap = average_precision_score(part["hit"], score)
            p10 = precision_at_10(part, score)
            metrics[f"{name}_ap_{model}"], metrics[f"{name}_p10_{model}"] = ap, p10
            print(f"  {model:9} PR-AUC {ap:.3f}   precision@10 per day {p10:.3f}")
        lo, hi = bootstrap(part["hit"], s["full"], s["baseline"])
        metrics[f"{name}_full_minus_baseline_lo"] = lo
        metrics[f"{name}_full_minus_baseline_hi"] = hi
        print(f"  full - baseline PR-AUC, 95 % interval: {lo:+.3f} to {hi:+.3f}")
    # the model to register is chosen on the validation week, never on the test period
    best = "full" if metrics["valid_ap_full"] > metrics["valid_ap_momentum"] else "momentum"

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
            {"train": TRAIN, "valid": VALID, "test": TEST, "rounds": ROUNDS, **PARAMS}
        )
        mlflow.log_params({"train_stories": len(train), "train_hits": int(train["hit"].sum())})
        mlflow.log_metrics(metrics)
        for model_name, model in (("momentum", momentum), ("full", full)):
            register = "hn-breakout" if final and model_name == best else None
            mlflow.lightgbm.log_model(model, name=model_name, registered_model_name=register)
