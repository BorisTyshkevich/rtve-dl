from __future__ import annotations

import struct
from dataclasses import dataclass

from rtve_dl.http import HttpClient


def _iter_png_chunks(png: bytes):
    # PNG signature
    if len(png) < 8 or png[:8] != b"\x89PNG\r\n\x1a\n":
        return
    off = 8
    while off + 8 <= len(png):
        (length,) = struct.unpack(">I", png[off : off + 4])
        ctype = png[off + 4 : off + 8]
        off += 8
        if off + length + 4 > len(png):
            return
        data = png[off : off + length]
        off += length
        crc = png[off : off + 4]
        off += 4
        yield (ctype, data, crc)
        if ctype == b"IEND":
            return

def _get_alfabet(t: str) -> str:
    # Port of Descargavideos' PNG_RTVE_Data::getAlfabet
    r = []
    e = 0
    n = 0
    for ch in t:
        if n == 0:
            r.append(ch)
            e = (e + 1) % 4
            n = e
        else:
            n -= 1
    return "".join(r)


def _get_url(texto: str, alfabeto: str) -> str:
    # Port of Descargavideos' PNG_RTVE_Data::getURL
    out = []
    a = 0
    n = 0
    s = 3
    h = 1
    for ch in texto:
        if n == 0:
            a = 10 * int(ch)
            n = 1
        else:
            if s == 0:
                a += int(ch)
                if 0 <= a < len(alfabeto):
                    out.append(alfabeto[a])
                s = (h + 3) % 4
                n = 0
                h += 1
            else:
                s -= 1
    return "".join(out)


def _decode_rtve_source(item: str) -> str | None:
    # Port of Descargavideos' PNG_RTVE_Data::getSource
    if "#" not in item:
        return None
    left, right = item.split("#", 1)
    n = _get_alfabet(left)
    return _get_url(right, n)


def extract_rtve_urls_from_thumbnail_png(png_bytes: bytes) -> list[str]:
    # RTVE sometimes returns the PNG as base64 text rather than raw PNG bytes.
    # Descargavideos base64-decodes before parsing.
    if not png_bytes.startswith(b"\x89PNG") and png_bytes[:16].strip().startswith(b"iVBOR"):
        try:
            import base64

            png_bytes = base64.b64decode(png_bytes, validate=False)
        except Exception:
            pass
    urls: list[str] = []
    for ctype, data, _crc in _iter_png_chunks(png_bytes):
        if ctype != b"tEXt":
            continue
        # Descargavideos builds a string ignoring NUL bytes across the entire chunk payload.
        # The payload contains something like "<alphabet>#<digits>%%..." encoded with interleaved NULs.
        data2 = data.replace(b"\x00", b"")
        try:
            h = data2.decode("latin-1", errors="replace")
        except Exception:
            continue
        # Normalize RTVE odd marker "%%" following "#".
        if "%%" in h and "#" in h:
            left, right = h.split("#", 1)
            if "%%" in right:
                right = right.split("%%", 1)[1]
                h = left + "#" + right
        u = _decode_rtve_source(h)
        if u:
            urls.append(u)
    # De-dupe while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


class RtveThumbnailResolver:
    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient()

    def resolve(self, asset_id: str) -> list[str]:
        # Desktop/high-quality first (rtveplayw), then fallback (default).
        urls = [
            f"https://ztnr.rtve.es/ztnr/movil/thumbnail/rtveplayw/videos/{asset_id}.png?q=v2",
            f"https://ztnr.rtve.es/ztnr/movil/thumbnail/default/videos/{asset_id}.png",
        ]
        found: list[str] = []
        for u in urls:
            r = self._http.get_bytes(u)
            if r.status_code >= 400 or not r.content:
                continue
            found.extend(extract_rtve_urls_from_thumbnail_png(r.content))
        # Filter obvious non-media junk and keep order.
        out: list[str] = []
        seen: set[str] = set()
        for u in found:
            if ".mpd" in u or "/tomcat/" in u:
                continue
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out
