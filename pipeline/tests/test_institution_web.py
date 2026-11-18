import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from pipeline.institution.web import PoliteJsonClient, WebError, cache_key, cached_get


class FakeOpener:
    """Scripted responses; records every request."""

    def __init__(self, *responses: tuple[int, Mapping[str, str], bytes]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def __call__(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, Mapping[str, str], bytes]:
        self.calls.append((url, headers))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def ok(payload: object) -> tuple[int, Mapping[str, str], bytes]:
    return 200, {}, json.dumps(payload).encode()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def client(opener: FakeOpener, clock: Clock, **kw: object) -> PoliteJsonClient:
    return PoliteJsonClient(opener=opener, sleep=clock.sleep, clock=clock, **kw)  # type: ignore[arg-type]


def test_sends_descriptive_user_agent_without_personal_data_and_encodes_params() -> None:
    opener, clock = FakeOpener(ok({"a": 1})), Clock()
    assert client(opener, clock).get_json("https://x.org/api", {"q": "a b", "n": 2}) == {"a": 1}
    url, headers = opener.calls[0]
    assert url == "https://x.org/api?q=a+b&n=2"
    assert (
        headers["User-Agent"].startswith("uiuc-research-portal/")
        and "@" not in headers["User-Agent"]
    )


def test_spaces_requests_by_min_interval() -> None:
    opener, clock = FakeOpener(ok({})), Clock()
    c = client(opener, clock, min_interval=2.0)
    c.get_json("https://x.org/1")
    c.get_json("https://x.org/2")
    assert clock.sleeps == [2.0]  # first request is immediate, second waits for the interval


def test_honours_retry_after_then_succeeds() -> None:
    opener = FakeOpener((429, {"Retry-After": "50"}, b""), ok({"done": True}))
    clock = Clock()
    assert client(opener, clock).get_json("https://x.org/a") == {"done": True}
    assert 50.0 in clock.sleeps and len(opener.calls) == 2


def test_exponential_backoff_without_retry_after_and_gives_up() -> None:
    opener, clock = FakeOpener((503, {}, b"")), Clock()
    with pytest.raises(WebError, match="HTTP 503"):
        client(opener, clock, max_retries=2, min_interval=0).get_json("https://x.org/a")
    assert len(opener.calls) == 3 and clock.sleeps == [2.0, 4.0]


@pytest.mark.parametrize("status", [403, 404, 500])
def test_other_errors_are_not_retried(status: int) -> None:
    opener, clock = FakeOpener((status, {}, b"nope")), Clock()
    with pytest.raises(WebError, match=f"HTTP {status}"):
        client(opener, clock).get_json("https://x.org/a")
    assert len(opener.calls) == 1


def test_rejects_plain_http_and_non_json() -> None:
    opener, clock = FakeOpener((200, {}, b"<html>")), Clock()
    with pytest.raises(WebError, match="non-https"):
        client(opener, clock).get_json("http://x.org/a")
    with pytest.raises(WebError, match="not JSON"):
        client(opener, clock).get_json("https://x.org/a")


def test_cached_get_fetches_each_url_once(tmp_path: Path) -> None:
    opener, clock = FakeOpener(ok({"v": 1}), ok({"v": 2})), Clock()
    c = client(opener, clock)
    assert cached_get(c, tmp_path, "wiki", "https://x.org/a", {"q": 1}) == {"v": 1}
    assert cached_get(c, tmp_path, "wiki", "https://x.org/a", {"q": 1}) == {"v": 1}  # from disk
    assert len(opener.calls) == 1
    assert cached_get(c, tmp_path, "wiki", "https://x.org/a", {"q": 2}) == {"v": 2}  # other params
    assert cached_get(c, tmp_path, "wiki", "https://x.org/a", {"q": 1}, refresh=True) == {"v": 2}
    assert cache_key("https://x.org/a", {"q": 1}) != cache_key("https://x.org/a", {"q": 2})
    assert cache_key("https://x.org/a", {"b": 1, "a": 2}) == cache_key(
        "https://x.org/a", {"a": 2, "b": 1}
    )
