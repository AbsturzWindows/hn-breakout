"""A fake Hacker News API (httpx.MockTransport), so tests need no network."""

import json

import httpx
import pytest

from hn_breakout import poll


class FakeHN:
    def __init__(self):
        self.items, self.users, self.top = {}, {}, []
        self.fail = {}  # path -> how many times it answers 500 before working
        self.requests = []

    def add_story(self, id_, time, score=1, by="alice", **extra):
        self.items[id_] = {
            "id": id_,
            "type": "story",
            "time": time,
            "score": score,
            "descendants": 0,
            "by": by,
            "title": f"Story {id_}",
            "url": f"https://example.com/{id_}",
            **extra,
        }

    def handle(self, request):
        path = request.url.path.removeprefix("/v0/").removesuffix(".json")
        self.requests.append(path)
        if self.fail.get(path, 0) > 0:
            self.fail[path] -= 1
            return httpx.Response(500)
        if path == "newstories":
            return httpx.Response(200, json=sorted(self.items, reverse=True))
        if path == "topstories":
            return httpx.Response(200, json=self.top)
        kind, key = path.split("/")
        data = self.items.get(int(key)) if kind == "item" else self.users.get(key)
        return httpx.Response(200, content=json.dumps(data))  # the real API sends "null" too

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))


@pytest.fixture
def hn():
    return FakeHN()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(poll.time, "sleep", lambda s: None)
