"""Polite, cached JSON client for public web APIs (Wikipedia, Wikidata, Commons, ROR).

Why ``urllib`` and not httpx here: Wikimedia answers every httpx request with 403 ("robot
policy") while the same request through urllib is accepted (verified 2026-11-18 on all
Wikimedia hosts). The stdlib needs no extra dependency.

Politeness: a minimum interval between requests, ``Retry-After`` honoured on 429/503, a
descriptive User-Agent *without* personal contact data, and every response cached on disk so
each URL is fetched once (``--refresh`` to redo).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

USER_AGENT = "uiuc-research-portal/0.1 (technical assessment project)"

# (url, headers, timeout) -> (status, response headers, body)
Opener = Callable[[str, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]


def urllib_opener(
    url: str, headers: Mapping[str, str], timeout: float
) -> tuple[int, Mapping[str, str], bytes]:
    req = urllib.request.Request(url, headers=dict(headers))  # noqa: S310  (https only, see below)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class WebError(RuntimeError):
    pass


class PoliteJsonClient:
    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        min_interval: float = 1.0,
        max_retries: int = 5,
        timeout: float = 30.0,
        opener: Opener = urllib_opener,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._timeout = timeout
        self._opener = opener
        self._sleep = sleep
        self._clock = clock
        self._last = float("-inf")
        self.requests = 0

    def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        if not url.startswith("https://"):
            raise WebError(f"refusing non-https URL: {url}")
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            self.requests += 1
            status, headers, body = self._opener(full, self._headers, self._timeout)
            if status == 200:
                try:
                    return json.loads(body)
                except ValueError:
                    raise WebError(f"{url}: response is not JSON") from None
            if status in (429, 503) and attempt < self._max_retries:
                retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else 2.0 ** (attempt + 1)
                log.warning("%s -> HTTP %s; retry in %.0fs", url, status, delay)
                self._sleep(delay)
                continue
            raise WebError(f"{url}: HTTP {status}")
        raise AssertionError("unreachable")  # pragma: no cover


def cache_key(url: str, params: Mapping[str, Any] | None) -> str:
    raw = url + "?" + urllib.parse.urlencode(sorted((params or {}).items()))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cached_get(
    client: PoliteJsonClient,
    cache_dir: Path,
    label: str,
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    refresh: bool = False,
) -> Any:
    """GET with an on-disk cache (``<label>-<hash>.json``): each URL is fetched once."""
    path = cache_dir / f"{label}-{cache_key(url, params)}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    data = client.get_json(url, params)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return data
