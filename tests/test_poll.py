from datetime import UTC, datetime, timedelta

import httpx

from hn_breakout import poll, storage

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
NOW_S = int(NOW.timestamp())


def test_get_retries_then_gives_up(hn):
    hn.add_story(1, NOW_S)
    hn.fail["item/1"] = 2
    assert poll.get(hn.client(), "item/1")["id"] == 1
    hn.fail["item/1"] = 3
    assert poll.get(hn.client(), "item/1") is None


def test_snapshots_only_recent_stories_and_stop_early(hn):
    for i in range(1, 101):  # one story per minute, newest is 100
        hn.add_story(i, NOW_S - (100 - i) * 60)
    df = poll.snapshots(hn.client(), NOW)
    assert set(df["story_id"]) == set(range(65, 101))
    assert sum(r.startswith("item/") for r in hn.requests) == 37  # stops at the first old one


def test_snapshots_skip_jobs_dead_deleted_and_add_karma_near_30_min(hn):
    hn.add_story(1, NOW_S - 28 * 60, score=12, by="alice")
    hn.add_story(2, NOW_S - 5 * 60, by="bob")
    hn.add_story(3, NOW_S - 10 * 60, type="job")
    hn.add_story(4, NOW_S - 10 * 60, dead=True)
    hn.items[5] = {"id": 5, "deleted": True}  # deleted items have no time
    hn.users = {"alice": {"karma": 500, "created": 1}}
    df = poll.snapshots(hn.client(), NOW).set_index("story_id")
    assert sorted(df.index) == [1, 2]
    assert df.loc[1, "karma"] == 500 and df.loc[1, "score"] == 12
    assert "user/bob" not in hn.requests  # too young for karma


def test_labels_once_after_24h(hn, tmp_path):
    hn.add_story(1, NOW_S - 10 * 60)
    hn.top = [1]
    report = poll.run(NOW, hn.client(), str(tmp_path))
    assert report["errors"] == "" and report["n_snapshots"] == 1 and report["n_frontpage"] == 1

    hn.items[1]["score"] = 250
    later = NOW + timedelta(hours=24)
    assert poll.run(later, hn.client(), str(tmp_path))["n_labels"] == 1
    assert (
        storage.read("labels", later.date(), later.date(), str(tmp_path))["final_score"][0] == 250
    )
    assert poll.run(later + timedelta(minutes=5), hn.client(), str(tmp_path))["n_labels"] == 0


def test_missed_runs_are_caught_up(hn, tmp_path):
    hn.add_story(1, NOW_S - 10 * 60)
    poll.run(NOW, hn.client(), str(tmp_path))
    late = NOW + timedelta(hours=25, minutes=20)  # job was down around the 24 h mark
    assert poll.run(late, hn.client(), str(tmp_path))["n_labels"] == 1


def test_api_outage_is_an_error_but_other_steps_still_run(hn, tmp_path):
    hn.add_story(1, NOW_S - 10 * 60)
    hn.fail["topstories"] = 99
    report = poll.run(NOW, hn.client(), str(tmp_path))
    assert report["errors"] == "frontpage: topstories unavailable"
    assert len(storage.read("snapshots", NOW.date(), NOW.date(), str(tmp_path))) == 1
    assert len(storage.read("runs", NOW.date(), NOW.date(), str(tmp_path))) == 1


def test_invalid_json_counts_as_failure():
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>"))
    )
    assert poll.get(http, "item/1") is None
