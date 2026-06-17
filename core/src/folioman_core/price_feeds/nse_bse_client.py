"""Shared NSE/BSE HTTP client — the single breakage surface for exchange feeds.

NSE and BSE expose only **unofficial JSON/CSV endpoints**. They sit behind a
cookie wall (a plain request is rejected until a browser-like session cookie is
minted by a prior page load), demand browser-like headers, and change shape /
throttle without notice. Rather than have every feed (price history, corporate
actions, dividends, quotes) re-implement the cookie warm-up, header set, and
retry policy, they all go through here — so a site change is a **one-module fix**.

What this owns:

- base URLs for NSE and BSE,
- the cookie warm-up (hit a real page to mint cookies before the data call),
- the browser-like header set and request timeout,
- the transient-retry/backoff policy: ``429`` / ``5xx`` / transport errors are
  retried with exponential backoff; any other status (incl. permanent ``4xx``) is
  returned as-is for the caller to handle — **fail fast, don't mask**.

The retry layer never inspects the body: an exchange that serves an HTML WAF page
with a ``200`` is *not* a transport failure, so retrying wouldn't help — the
caller (which knows the expected content-type/shape) decides that.
"""

from __future__ import annotations

import contextlib
import time
from types import TracebackType

import httpx

NSE_BASE_URL = "https://www.nseindia.com"
BSE_BASE_URL = "https://www.bseindia.com"
DEFAULT_TIMEOUT = 30.0
MAX_REDIRECTS = 5
# Cap exchange JSON bodies before parsing — date windows are chunked, not bytes.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# NSE/BSE reject empty / non-browser User-Agents; mimic a real client. Kept in one
# place so a required-header change is a single edit.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15"
        " (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/csv, application/json, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# A failed warm-up page load is non-fatal; the data call still attempts.
_NSE_WARMUP_PATH = "/get-quotes/equity"
_NSE_WARMUP_PARAMS = {"symbol": "RELIANCE"}
_BSE_WARMUP_PATH = "/"

# Transient-retry/backoff policy. Statuses NSE/BSE return under load or throttling;
# anything else (incl. permanent 4xx) is handed back without retry.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5  # seconds; attempt n waits _BACKOFF_BASE * 2**n
_SLEEP = time.sleep


class ResponseTooLargeError(httpx.HTTPError):
    """An exchange response body exceeded ``MAX_RESPONSE_BYTES``.

    Subclasses ``httpx.HTTPError`` so existing callers (which already handle
    ``raise_for_status`` / ``httpx.HTTPError``) treat an oversized body like any
    other failed request — recorded and moved past, never retried (retrying would
    just re-download it) and never buffered whole.
    """


class ExchangeClient:
    """A cookie-warmed, browser-headed httpx client with transient retry.

    Wraps a single ``httpx.Client`` so a batch reuses one warmed session. Exposes
    :meth:`get`, which retries transient failures and otherwise returns the
    response untouched — callers keep doing their own ``raise_for_status`` /
    content-type / shape checks.
    """

    def __init__(
        self,
        *,
        base_url: str,
        warmup_path: str,
        warmup_params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url
        self._warmup_path = warmup_path
        self._warmup_params = warmup_params or {}
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url,
            headers=headers or BROWSER_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
        )

    @property
    def http(self) -> httpx.Client:
        """The underlying httpx client (cookies, base_url) for advanced callers."""
        return self._client

    def warm(self) -> ExchangeClient:
        """Mint session cookies via a real page load. Non-fatal on failure."""
        with contextlib.suppress(httpx.HTTPError):
            self._client.get(self._warmup_path, params=self._warmup_params)
        return self

    def get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """GET ``path``, retrying only transient (429/5xx/transport) failures.

        A transient HTTP status is retried with exponential backoff and, if it
        never clears, the last response is returned so the caller can
        ``raise_for_status``. A transport error that never clears is re-raised.
        Every other response (success or permanent ``4xx``) is returned at once.
        """
        last_response: httpx.Response | None = None
        last_exc: httpx.TransportError | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._capped_get(path, params=params, headers=headers)
            except httpx.TransportError as exc:
                last_exc = exc
                last_response = None
            else:
                if response.status_code not in _RETRY_STATUSES:
                    return response
                last_response = response
                last_exc = None
            if attempt < _MAX_RETRIES:
                _SLEEP(_BACKOFF_BASE * (2**attempt))
        if last_response is not None:
            return last_response
        raise last_exc  # transport error that never cleared

    def _capped_get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """GET with a hard body-size cap, returned as a buffered response.

        NSE/BSE are untrusted: a plain ``.get`` reads the whole body into memory
        before any size check, so a hostile/compromised endpoint (or a gzip bomb)
        can OOM the process. Stream instead and abort once the *decoded* bytes pass
        ``MAX_RESPONSE_BYTES`` — ``Content-Length`` is only a hint (it can be absent
        or lied about, and counts compressed bytes), so the streamed tally is the
        real guard. The capped body is rebuilt into a normal ``httpx.Response`` so
        callers keep using ``.json()`` / ``.content`` / ``raise_for_status``.
        """
        with self._client.stream("GET", path, params=params, headers=headers) as response:
            declared = response.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                msg = f"exchange response too large (declared {declared} bytes)"
                raise ResponseTooLargeError(msg)
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    msg = f"exchange response exceeded {MAX_RESPONSE_BYTES} bytes"
                    raise ResponseTooLargeError(msg)
                chunks.append(chunk)
        # ``iter_bytes`` already decoded the body, so the original transfer headers
        # no longer describe it; drop them and let httpx set them from ``content``.
        clean_headers = [
            (k, v)
            for k, v in response.headers.multi_items()
            if k.lower() not in ("content-encoding", "content-length")
        ]
        return httpx.Response(
            status_code=response.status_code,
            headers=clean_headers,
            content=b"".join(chunks),
            request=response.request,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ExchangeClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def nse_client(*, client: httpx.Client | None = None) -> ExchangeClient:
    """A warmed NSE client. Pass ``client`` to wrap a pre-built httpx client."""
    return ExchangeClient(
        base_url=NSE_BASE_URL,
        warmup_path=_NSE_WARMUP_PATH,
        warmup_params=_NSE_WARMUP_PARAMS,
        client=client,
    ).warm()


def bse_client(*, client: httpx.Client | None = None) -> ExchangeClient:
    """A warmed BSE client. Pass ``client`` to wrap a pre-built httpx client."""
    return ExchangeClient(
        base_url=BSE_BASE_URL,
        warmup_path=_BSE_WARMUP_PATH,
        client=client,
    ).warm()
