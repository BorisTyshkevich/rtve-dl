from __future__ import annotations

import json
import re
import gzip
import zlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_HEADERS = {
    # RTVE often behaves differently without a browser-ish UA.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
}


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status_code: int
    content: bytes
    headers: dict[str, str]


_CHARSET_RE = re.compile(r"charset=([^\s;]+)", re.IGNORECASE)


def _guess_encoding(headers: dict[str, str], default: str = "utf-8") -> str:
    ct = headers.get("content-type") or headers.get("Content-Type") or ""
    m = _CHARSET_RE.search(ct)
    return m.group(1) if m else default


class HttpClient:
    def __init__(self, timeout_s: float = 30.0, retries: int = 3) -> None:
        self._timeout_s = timeout_s
        self._retries = retries
        self._headers = dict(DEFAULT_HEADERS)

    def get_bytes(self, url: str) -> HttpResponse:
        req = urllib.request.Request(url, headers=self._headers, method="GET")
        last: HttpResponse | None = None
        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout_s) as r:
                    raw = r.read()
                    headers = dict(r.headers.items())
                    ce = (headers.get("Content-Encoding") or headers.get("content-encoding") or "").lower()
                    if ce == "gzip":
                        content = gzip.decompress(raw)
                    elif ce == "deflate":
                        # zlib-wrapped or raw deflate; try both.
                        try:
                            content = zlib.decompress(raw)
                        except zlib.error:
                            content = zlib.decompress(raw, -zlib.MAX_WBITS)
                    else:
                        content = raw
                    return HttpResponse(
                        url=r.geturl(),
                        status_code=getattr(r, "status", 200),
                        content=content,
                        headers=headers,
                    )
            except urllib.error.HTTPError as e:
                last = HttpResponse(
                    url=getattr(e, "url", url),
                    status_code=e.code,
                    content=e.read() if hasattr(e, "read") else b"",
                    headers=dict(getattr(e, "headers", {}) or {}),
                )
                # Retry transient server errors.
                if attempt < self._retries and last.status_code >= 500:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return last
            except urllib.error.URLError:
                if attempt < self._retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise
        assert last is not None
        return last

    def get_text(self, url: str, encoding: str | None = None) -> str:
        r = self.get_bytes(url)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {url} failed: HTTP {r.status_code}")
        enc = encoding or _guess_encoding(r.headers)
        return r.content.decode(enc, errors="replace")

    def get_json(self, url: str) -> Any:
        return json.loads(self.get_text(url))
