from datetime import date

import httpx
import pytest

from hn_breakout import backfill


def algolia(n_hits, n_total=None):
    """A fake Algolia that answers every request with n_hits stories."""
    requests = []

    def handle(request):
        requests.append(request)
        hits = [
            {
                "objectID": str(len(requests) * 100 + i),
                "created_at_i": 0,
                "title": "T",
                "url": "https://example.com",
                "author": "alice",
                "points": 120,
            }
            for i in range(n_hits)
        ]
        return httpx.Response(200, json={"nbHits": n_total or n_hits, "hits": hits})

    return httpx.Client(transport=httpx.MockTransport(handle)), requests


def test_backfill_day_asks_every_hour():
    http, requests = algolia(2)
    df = backfill.backfill_day(http, date(2026, 10, 1))
    assert len(requests) == 24 and len(df) == 48
    assert df["story_id"].is_unique and (df["final_score"] == 120).all()
    first = requests[0].url.params["numericFilters"]
    assert first == "created_at_i>=1790812800,created_at_i<1790816400"  # 2026-10-01 00:00-01:00 UTC


def test_incomplete_answer_is_an_error():
    http, _ = algolia(2, n_total=1500)
    with pytest.raises(RuntimeError):
        backfill.stories(http, 0, 3600)


def test_slow_answer_is_retried(monkeypatch):
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"nbHits": 0, "hits": []})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    assert backfill.stories(http, 0, 3600) == [] and len(calls) == 2
