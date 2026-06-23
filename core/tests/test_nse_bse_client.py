"""Shared NSE/BSE client — cookie warm-up + transient retry/backoff. HTTP mocked.

This is the single breakage surface for the exchange feeds, so the contract it
guarantees (warm-up minted before the data call; 429/5xx/transport retried;
permanent 4xx and HTML-WAF 200s handed straight back) is pinned here.
"""

import httpx
import pytest
from folioman_core.price_feeds import nse_bse_client
from folioman_core.price_feeds.nse_bse_client import ExchangeClient, bse_client, nse_client


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(nse_bse_client, "_SLEEP", lambda _s: None)


def _wrap(handler, **kw) -> ExchangeClient:
    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=nse_bse_client.NSE_BASE_URL
    )
    return ExchangeClient(
        base_url=nse_bse_client.NSE_BASE_URL, warmup_path="/warm", client=http, **kw
    )


def test_warm_mints_cookies_before_data_call():
    paths: list[str] = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    client = ExchangeClient(
        base_url=nse_bse_client.NSE_BASE_URL,
        warmup_path="/warm",
        warmup_params={"symbol": "RELIANCE"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url=nse_bse_client.NSE_BASE_URL
        ),
    ).warm()
    client.get("/data")
    assert paths == ["/warm", "/data"]  # warm-up happened first


def test_warmup_failure_is_non_fatal():
    def handler(request):
        if request.url.path == "/warm":
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": True})

    client = _wrap(handler)
    client.warm()  # swallowed
    assert client.get("/data").status_code == 200


def test_permanent_4xx_returned_without_retry():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404, text="nope")

    resp = _wrap(handler).get("/data")
    assert resp.status_code == 404
    assert calls["n"] == 1  # fail fast, no retry


def test_success_returned_without_retry():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    assert _wrap(handler).get("/data").status_code == 200
    assert calls["n"] == 1


def test_html_waf_200_not_retried():
    # An HTML WAF page with a 200 isn't a transport failure — retrying won't help;
    # the caller (which knows the expected shape) decides. So: returned as-is.
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(
            200, text="<html>blocked</html>", headers={"Content-Type": "text/html"}
        )

    resp = _wrap(handler).get("/data")
    assert calls["n"] == 1
    assert "blocked" in resp.text


def test_transient_5xx_retried_then_succeeds():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True})

    resp = _wrap(handler).get("/data")
    assert resp.status_code == 200
    assert calls["n"] == 3  # two retries before success


def test_429_retried_and_last_response_returned_when_never_clears():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(429, text="rate limited")

    resp = _wrap(handler).get("/data")
    assert resp.status_code == 429  # caller raises_for_status on this
    assert calls["n"] == nse_bse_client._MAX_RETRIES + 1


def test_transport_error_retried_then_reraised():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        _wrap(handler).get("/data")
    assert calls["n"] == nse_bse_client._MAX_RETRIES + 1


def test_response_size_cap_rejects_declared_oversize(monkeypatch):
    # A body whose Content-Length already exceeds the cap is rejected before any
    # bytes are buffered.
    monkeypatch.setattr(nse_bse_client, "MAX_RESPONSE_BYTES", 100)

    def handler(request):
        return httpx.Response(200, content=b"x" * 500)

    with pytest.raises(nse_bse_client.ResponseTooLargeError):
        _wrap(handler).get("/data")


def test_response_size_cap_streams_and_aborts_without_content_length(monkeypatch):
    # An iterator body is chunked (no Content-Length), so the streamed byte tally
    # is the only guard — mirrors a hostile endpoint that omits/lies about length.
    monkeypatch.setattr(nse_bse_client, "MAX_RESPONSE_BYTES", 100)

    def handler(request):
        return httpx.Response(200, content=iter([b"x" * 60, b"x" * 60]))

    with pytest.raises(nse_bse_client.ResponseTooLargeError):
        _wrap(handler).get("/data")


def test_capped_get_preserves_body_under_cap():
    # The rebuilt buffered response still exposes JSON + content-type for callers.
    def handler(request):
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    resp = _wrap(handler).get("/data")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert "application/json" in resp.headers.get("content-type", "")


def test_factories_warm_and_target_each_exchange():
    nse_paths: list[str] = []
    bse_paths: list[str] = []

    def nse_handler(request):
        nse_paths.append(request.url.path)
        return httpx.Response(200, json={})

    def bse_handler(request):
        bse_paths.append(request.url.path)
        return httpx.Response(200, json={})

    nse_client(
        client=httpx.Client(
            transport=httpx.MockTransport(nse_handler), base_url=nse_bse_client.NSE_BASE_URL
        )
    )
    bse_client(
        client=httpx.Client(
            transport=httpx.MockTransport(bse_handler), base_url=nse_bse_client.BSE_BASE_URL
        )
    )
    assert nse_paths == ["/get-quotes/equity"]
    assert bse_paths == ["/"]
