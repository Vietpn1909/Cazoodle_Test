import httpx
import pytest

from pipeline.openalex.client import BudgetExhaustedError, OpenAlexClient, OpenAlexError

BUDGET_HEADERS = {"x-ratelimit-remaining-usd": "0.05", "x-ratelimit-cost-usd": "0.0001"}


def make_client(handler: httpx.MockTransport, **kw: object) -> OpenAlexClient:
    return OpenAlexClient(transport=handler, sleep=lambda _: None, **kw)  # type: ignore[arg-type]


def test_retries_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True}, headers=BUDGET_HEADERS)

    with make_client(httpx.MockTransport(handler)) as client:
        assert client.get("/works") == {"ok": True}
        assert calls["n"] == 3
        assert client.last_budget.remaining_usd == 0.05


def test_budget_exhausted_stops_without_retry() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429, headers={"x-ratelimit-remaining-usd": "0", "x-ratelimit-reset": "7200"}
        )

    with make_client(httpx.MockTransport(handler)) as client, pytest.raises(BudgetExhaustedError):
        client.get("/works")
    assert calls["n"] == 1


def test_api_key_sent_but_never_in_error_message() -> None:
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.url.params.get("api_key")
        return httpx.Response(400, text=f"bad request for {req.url}")

    client = make_client(httpx.MockTransport(handler), api_key="SECRET123")
    with client, pytest.raises(OpenAlexError) as exc:
        client.get("/works", {"filter": "x"})
    assert seen["key"] == "SECRET123"
    assert "SECRET123" not in str(exc.value)
