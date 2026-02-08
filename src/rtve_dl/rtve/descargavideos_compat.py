from __future__ import annotations

import base64
import re
from dataclasses import dataclass

from rtve_dl.http import HttpClient
from rtve_dl.rtve.png_thumbnail import RtveThumbnailResolver


_BAD_SUBSTRS = ("1100000000000", "l3-onlinefs.rtve.es", ".mpd", ".vcl", "/tomcat/")


def _urlsafe_b64encode(b: bytes) -> str:
    s = base64.b64encode(b).decode("ascii")
    return s.replace("+", "-").replace("/", "_")


def _urlsafe_b64decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad
    return base64.b64decode(s)


def _pkcs5_pad(b: bytes, block: int = 8) -> bytes:
    n = block - (len(b) % block)
    return b + bytes([n]) * n


def _pkcs5_unpad(b: bytes) -> bytes:
    if not b:
        return b
    n = b[-1]
    if n < 1 or n > 8:
        return b
    if b[-n:] != bytes([n]) * n:
        return b
    return b[:-n]


def _blowfish_ecb_encrypt(key: bytes, plaintext: bytes) -> bytes:
    # Optional dependency: cryptography (widely available).
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.Blowfish(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(plaintext) + enc.finalize()


def _blowfish_ecb_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.Blowfish(key), modes.ECB())
    dec = cipher.decryptor()
    return dec.update(ciphertext) + dec.finalize()


def dv_encripta(s: str, key: str = "yeL&daD3") -> str:
    """
    Port of Descargavideos' Rtve::encripta (BLOWFISH-ECB, PKCS5 padding, base64, urlsafe char replacements).
    """
    pt = _pkcs5_pad(s.encode("utf-8"), 8)
    ct = _blowfish_ecb_encrypt(key.encode("utf-8"), pt)
    return _urlsafe_b64encode(ct)


def dv_desencripta(s: str, key: str = "yeL&daD3") -> str:
    """
    Port of Descargavideos' Rtve::desencripta + b64d.
    Returns a best-effort decoded text blob (latin-1 fallback).
    """
    ct = _urlsafe_b64decode(s)
    pt = _blowfish_ecb_decrypt(key.encode("utf-8"), ct)
    pt = _pkcs5_unpad(pt)
    try:
        return pt.decode("utf-8", errors="replace")
    except Exception:
        return pt.decode("latin-1", errors="replace")


def _find_urls(raw: list[str], want_m3u8: bool) -> list[str]:
    out: list[str] = []
    for u in raw:
        if any(b in u for b in _BAD_SUBSTRS):
            continue
        if re.search(r"\.mp4/.*\.m3u8", u):
            # Remove trailing playlist part, keep mp4 base.
            m = re.search(r"(https?://.*?\.mp4)", u)
            if m:
                out.append(m.group(1))
            continue

        is_m3u8 = ".m3u8" in u
        if want_m3u8 and (not is_m3u8 or ".rtve.es/" not in u):
            continue
        if (not want_m3u8) and is_m3u8:
            continue
        out.append(u)
    # De-dupe preserve order.
    dedup: list[str] = []
    seen: set[str] = set()
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def dv_resolve_urls(asset_id: str, http: HttpClient | None = None) -> list[str]:
    """
    Simplified port of Descargavideos' Rtve::convierteID.

    Order:
    1) Try thumbnail PNG decoding.
    2) Fallback to ztnr/res/<encrypted> method.
    """
    http = http or HttpClient()
    links: list[str] = []

    # 1) thumbnail
    thumb = RtveThumbnailResolver(http)
    raw = thumb.resolve(asset_id)
    if raw:
        links.extend(_find_urls(raw, want_m3u8=False))
        links.extend(_find_urls(raw, want_m3u8=True))

    # 2) ztnr/res encrypted
    if not links:
        for modo in ("video", "audio"):
            codificado = dv_encripta(f"{asset_id}_banebdyede_{modo}_es")
            url = f"https://ztnr.rtve.es/ztnr/res/{codificado}"
            resp = http.get_text(url)
            content = dv_desencripta(resp)
            raw2 = re.findall(r"http://[^<>\"]+?\.(?:mp4|mp3)[^<>\"]*", content)
            raw2 += re.findall(r"https?://[^<>\"]+?\.(?:mp4|mp3|m3u8)[^<>\"]*", content)
            if raw2:
                links.extend(_find_urls(raw2, want_m3u8=False))
                links.extend(_find_urls(raw2, want_m3u8=True))
            if links:
                break

    # Final ordering preference: m3u8 master, then other m3u8, then mp4.
    links = [u for i, u in enumerate(links) if u not in links[:i]]
    def score(u: str) -> tuple[int, int]:
        if u.endswith(".m3u8") and "video.m3u8" in u:
            return (0, 0)
        if ".m3u8" in u:
            return (1, 0)
        if ".mp4" in u:
            return (2, 0)
        return (3, 0)
    links.sort(key=score)
    return links


@dataclass(frozen=True)
class DvResolved:
    asset_id: str
    video_urls: list[str]


def dv_resolve_from_rtve_url(rtve_url: str, http: HttpClient | None = None) -> DvResolved:
    m = re.search(r"/(\\d{4,})/?$", rtve_url.rstrip("/") + "/")
    if not m:
        # fallback: any /<digits>/ in url
        m = re.search(r"/(\\d{4,})/", rtve_url)
    if not m:
        raise ValueError("could not extract asset id from url")
    asset_id = m.group(1)
    urls = dv_resolve_urls(asset_id, http=http)
    return DvResolved(asset_id=asset_id, video_urls=urls)

