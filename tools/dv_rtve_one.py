#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path


def _extract_asset_id(url: str) -> str:
    m = re.search(r"/(\d{4,})/?$", url.rstrip("/") + "/")
    if not m:
        m = re.search(r"/(\d{4,})/", url)
    if not m:
        raise SystemExit("could not extract asset id from URL")
    return m.group(1)


def _pick_best_rtve_mp4(urls: list[str]) -> str:
    # Prefer direct progressive mp4 on rtve-mediavod-lote3, else any mp4.
    for u in urls:
        if "rtve-mediavod-lote3.rtve.es" in u and ".mp4" in u:
            return u
    for u in urls:
        if ".mp4" in u:
            return u
    return urls[0]


def _curl_download(url: str, out_path: Path, *, referer: str, range_bytes: str | None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["curl", "-L", "-f", "--retry", "3", "--retry-delay", "2", "-o", str(out_path)]
    cmd += ["-H", "user-agent: Mozilla/5.0", "-H", f"referer: {referer}"]
    if range_bytes:
        cmd += ["-H", f"range: bytes={range_bytes}"]
    cmd.append(url)
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise SystemExit("curl download failed")


def main() -> int:
    ap = argparse.ArgumentParser(description="Minimal RTVE downloader ported from Descargavideos logic.")
    ap.add_argument("url", help="RTVE episode URL (e.g. https://www.rtve.es/play/videos/.../880355/)")
    ap.add_argument("--out", default=None, help="Output file path (default: ./<asset_id>.mp4)")
    ap.add_argument("--referer", default="https://www.rtve.es/", help="HTTP Referer header")
    ap.add_argument(
        "--range",
        default="0-1048575",
        help="Byte range for test download (default: 1MiB, '0-1048575'). Use empty to download full file.",
    )
    args = ap.parse_args()

    asset_id = _extract_asset_id(args.url)

    # Import from the main project (run from repo root): python3 tools/dv_rtve_one.py ...
    import sys

    sys.path.insert(0, "src")
    from rtve_dl.rtve.descargavideos_compat import dv_resolve_urls

    urls = dv_resolve_urls(asset_id)
    if not urls:
        raise SystemExit("no media URLs resolved")

    mp4_url = _pick_best_rtve_mp4(urls)
    out = Path(args.out) if args.out else Path(f"{asset_id}.mp4")

    rng = args.range if args.range else None
    if rng is not None:
        r = rng.strip()
        if r.isdigit():
            # Common mistake: passing a single number. Interpret as a size in bytes.
            n = int(r)
            if n <= 0:
                rng = None
            else:
                rng = f"0-{n-1}"
        else:
            if "-" not in r:
                raise SystemExit("--range must look like '0-1048575' (or empty for full download)")
    _curl_download(mp4_url, out, referer=args.referer, range_bytes=rng)

    size = out.stat().st_size
    print(f"asset_id={asset_id}")
    print(f"url={mp4_url}")
    print(f"downloaded={out} bytes={size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
