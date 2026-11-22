import pandas as pd

from hn_breakout.features import (
    PRIOR_WEIGHT,
    attach_labels,
    domain_hit_rate,
    domain_of,
    story_features,
)

T0 = 1_790_000_000  # submission time of story 1


def snaps(
    story_id, time, points_by_age_min, karma=None, title="A title", url="https://www.github.com/x"
):
    rows = []
    for age_min, score in points_by_age_min.items():
        rows.append(
            {
                "story_id": story_id,
                "time": time,
                "age_s": age_min * 60,
                "score": score,
                "descendants": score // 2,
                "title": title,
                "url": url,
                "by": "alice",
                "karma": karma if age_min >= 25 else None,
                "user_created": time - 10 * 86400 if age_min >= 25 else None,
            }
        )
    return rows


def test_domain_of():
    assert domain_of("https://www.github.com/a/b") == "github.com"
    assert domain_of(None) == "self"


def test_momentum_uses_last_snapshot_at_or_before_checkpoint():
    df = pd.DataFrame(snaps(1, T0, {3: 1, 8: 4, 13: 6, 18: 9, 23: 12, 28: 20, 33: 99}, karma=300))
    f = story_features(df).iloc[0]
    assert (f["points_10"], f["points_20"], f["points_30"]) == (4, 9, 20)
    assert f["gain_20_30"] == 11
    assert f["comments_30"] == 10  # 20 // 2, the 33-minute snapshot is never used
    assert f["karma"] == 300 and f["account_age_days"] == 10
    assert f["complete"] == 1 and f["late_first_seen"] == 0
    assert f["domain"] == "github.com"


def test_gap_in_polling_makes_story_incomplete():
    # nothing between minute 8 and minute 28 -> the 20-minute value is too stale
    df = pd.DataFrame(snaps(1, T0, {3: 1, 8: 4, 28: 20}))
    f = story_features(df).iloc[0]
    assert pd.isna(f["points_20"])
    assert f["complete"] == 0


def test_late_first_seen_flags_reposts():
    df = pd.DataFrame(snaps(1, T0, {14: 30, 19: 35, 24: 40, 29: 45}))
    assert story_features(df).iloc[0]["late_first_seen"] == 1


def test_title_flags_and_concurrency():
    rows = (
        snaps(1, T0, {5: 1, 10: 2}, title="Show HN: 3 tools?")
        + snaps(2, T0 + 600, {5: 1}, title="Ask HN: why", url=None)
        + snaps(3, T0 + 3600, {5: 1})
    )
    f = story_features(pd.DataFrame(rows)).set_index("story_id")
    assert f.loc[1, "is_show_hn"] == 1 and f.loc[1, "has_number"] == 1
    assert f.loc[1, "has_question_mark"] == 1
    assert f.loc[2, "is_ask_hn"] == 1 and f.loc[2, "has_url"] == 0 and f.loc[2, "domain"] == "self"
    assert f.loc[1, "n_concurrent"] == 1  # story 2 came 10 min later, story 3 an hour later


def test_attach_labels_drops_unlabelled_and_sets_hit():
    feats = story_features(pd.DataFrame(snaps(1, T0, {5: 1}) + snaps(2, T0, {5: 1})))
    labeled_at = pd.Timestamp(T0 + 86400, unit="s", tz="UTC")
    labels = pd.DataFrame({"story_id": [1], "final_score": [120], "labeled_at": [labeled_at]})
    out = attach_labels(feats, labels)
    assert list(out["story_id"]) == [1] and out.loc[0, "hit"] == 1
    assert out.loc[0, "labeled_at"] == T0 + 86400  # unix seconds, like `time`


def test_only_snapshots_older_than_30_min_gives_empty_frame():
    assert story_features(pd.DataFrame(snaps(1, T0, {33: 5}))).empty


def test_domain_hit_rate_ignores_labels_from_after_the_prediction():
    story = pd.DataFrame({"time": [T0], "domain": ["a.com"]})
    known = T0 + 30 * 60 - 1  # just before the prediction time
    labelled = pd.DataFrame(
        {"domain": ["a.com", "b.com"], "hit": [1, 0], "labeled_at": [known, known]}
    )
    before = domain_hit_rate(story, labelled).iloc[0]
    late = pd.DataFrame({"domain": ["a.com"], "hit": [1], "labeled_at": [T0 + 30 * 60]})
    after = domain_hit_rate(story, pd.concat([labelled, late])).iloc[0]
    assert before == after == (1 + PRIOR_WEIGHT * 0.5) / (1 + PRIOR_WEIGHT)


def test_domain_hit_rate_smooths_towards_the_overall_rate():
    stories = pd.DataFrame({"time": [T0, T0], "domain": ["big.com", "new.com"]})
    labelled = pd.DataFrame(
        {
            "domain": ["big.com"] * 100 + ["x.com"] * 100,
            "hit": [1] * 50 + [0] * 150,
            "labeled_at": [T0 - 1] * 200,
        }
    )
    big, new = domain_hit_rate(stories, labelled)
    assert new == 0.25  # no label of its own: the overall rate
    assert 0.25 < big < 0.5  # its own 0.5, pulled a little towards 0.25
    assert domain_hit_rate(stories, labelled.iloc[:0]).isna().all()  # nothing known yet
