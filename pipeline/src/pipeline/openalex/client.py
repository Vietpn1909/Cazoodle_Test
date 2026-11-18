"""Minimal, budget-aware OpenAlex HTTP client.

OpenAlex meters usage in USD per day (see `.claude/docs/openalex.md`). The client:
- retries 429/5xx/transport errors with exponential backoff, honoring ``Retry-After``;
- stops immediately (``BudgetExhaustedError``) when the daily budget is used up, instead of
  hammering the API;
- records the latest budget headers so callers can report remaining budget;
- never lets the API key leak into logs or exception messages.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

# httpx logs full request URLs (incl. query string) at INFO -> would leak `api_key`.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_URL = "https://api.openalex.org"
USER_AGENT = "uiuc-research-portal/0.1 (+https://github.com/)"


class OpenAlexError(RuntimeError):
    """Non-retryable API failure (message never contains the API key)."""


class BudgetExhaustedError(OpenAlexError):
    """Daily USD budget used up; resume after the reset time."""

    def __init__(self, reset_in_seconds: int | None) -> None:
        self.reset_in_seconds = reset_in_seconds
        hint = f" (resets in ~{reset_in_seconds // 3600}h)" if reset_in_seconds else ""
        super().__init__(f"OpenAlex daily budget exhausted{hint}. Re-run later to resume.")


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Budget:
    remaining_usd: float | None = None
    cost_usd: float | None = None
    reset_in_seconds: int | None = None

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> Budget:
        return cls(
            remaining_usd=_float(headers.get("x-ratelimit-remaining-usd")),
            cost_usd=_float(headers.get("x-ratelimit-cost-usd")),
            reset_in_seconds=_int(headers.get("x-ratelimit-reset")),
        )


class OpenAlexClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 120.0,
        max_retries: int = 5,
        backoff_base: float = 2.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )
        self.last_budget = Budget()
        self.calls = 0
        self.spent_usd = 0.0

    def __enter__(self) -> OpenAlexClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        explicit = _float(retry_after)
        return explicit if explicit is not None else float(self._backoff_base**attempt)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = dict(params or {})
        if self._api_key:
            query["api_key"] = self._api_key

        for attempt in range(self._max_retries + 1):
            last_try = attempt == self._max_retries
            try:
                resp = self._http.get(path, params=query)
            except httpx.TransportError as exc:
                if last_try:
                    raise OpenAlexError(f"GET {path}: network error {type(exc).__name__}") from None
                wait = self._backoff(attempt, None)
                log.warning("GET %s network error (%s); retry in %.0fs", path, exc, wait)
                self._sleep(wait)
                continue

            self.calls += 1
            self.last_budget = Budget.from_headers(resp.headers)
            if self.last_budget.cost_usd:
                self.spent_usd += self.last_budget.cost_usd

            if resp.status_code == 429:
                remaining = self.last_budget.remaining_usd
                if remaining is not None and remaining <= 0:
                    raise BudgetExhaustedError(self.last_budget.reset_in_seconds)
            if resp.status_code == 429 or resp.status_code >= 500:
                if last_try:
                    raise OpenAlexError(f"GET {path}: HTTP {resp.status_code} after retries")
                wait = self._backoff(attempt, resp.headers.get("retry-after"))
                log.warning("GET %s -> HTTP %s; retry in %.0fs", path, resp.status_code, wait)
                self._sleep(wait)
                continue
            if resp.status_code >= 400:
                snippet = resp.text[:200].replace(self._api_key or "\0", "***")
                raise OpenAlexError(f"GET {path}: HTTP {resp.status_code}: {snippet}")

            try:
                data: dict[str, Any] = resp.json()
            except ValueError:
                # Observed: a 200 response truncated mid-body (~2.2 MB page) -> transient.
                if last_try:
                    raise OpenAlexError(f"GET {path}: invalid JSON after retries") from None
                wait = self._backoff(attempt, None)
                log.warning("GET %s returned invalid/truncated JSON; retry in %.0fs", path, wait)
                self._sleep(wait)
                continue
            return data

        raise AssertionError("unreachable")  # pragma: no cover
