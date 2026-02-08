from __future__ import annotations

import subprocess
import urllib.parse
from pathlib import Path

from rtve_dl.ffmpeg import mux_mkv
from rtve_dl.lexicon.store import SeriesStore
from rtve_dl.workflows.subtitles import build_subtitles_for_selector


def _strip_query_param(url: str, key: str) -> str:
    p = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    q2 = [(k, v) for (k, v) in q if k != key]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q2), p.fragment))


def _curl_download(url: str, out_path: Path, *, cookie_file: str | None, cookie: str | None, headers: list[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["curl", "-L", "-f", "--retry", "3", "--retry-delay", "2"]
    if cookie_file:
        cmd += ["-b", cookie_file]
    if cookie:
        cmd += ["-b", cookie]
    for h in headers:
        cmd += ["-H", h]

    # Resume if partial file exists.
    if out_path.exists() and out_path.stat().st_size > 0:
        cmd += ["-C", "-"]

    cmd += ["-o", str(out_path), url]
    p = subprocess.run(cmd)
    if p.returncode != 0:
        # Common case: URL includes an expired download-token but is otherwise reachable with user cookies.
        url2 = _strip_query_param(url, "download-token")
        if url2 != url:
            cmd2 = cmd[:-2] + [url2] if cmd[-2] == str(out_path) else cmd
            # Rebuild command more robustly.
            cmd2 = ["curl", "-L", "-f", "--retry", "3", "--retry-delay", "2"]
            if cookie_file:
                cmd2 += ["-b", cookie_file]
            if cookie:
                cmd2 += ["-b", cookie]
            for h in headers:
                cmd2 += ["-H", h]
            if out_path.exists() and out_path.stat().st_size > 0:
                cmd2 += ["-C", "-"]
            cmd2 += ["-o", str(out_path), url2]
            p2 = subprocess.run(cmd2)
            if p2.returncode == 0:
                return
        raise RuntimeError("curl download failed (check cookies/headers/url)")


def mux_from_url(
    *,
    series_slug: str,
    selector: str,
    url: str,
    cookie_file: str | None,
    cookie: str | None,
    headers: list[str],
    out: str | None,
    with_ru: bool,
    require_ru: bool,
) -> int:
    store = SeriesStore.open_existing(series_slug)
    build_subtitles_for_selector(series_slug, selector, with_ru=(with_ru or require_ru), require_ru=require_ru)

    tmp_dir = store.root_dir / "tmp"
    spa = sorted(tmp_dir.glob("S??E??_*.spa.srt"))
    if not spa:
        raise SystemExit("no subtitles found; run `rtve_dl subs` first")
    base = spa[0].name[: -len(".spa.srt")]

    mp4_path = tmp_dir / f"{base}.url.mp4"
    if not mp4_path.exists():
        _curl_download(url, mp4_path, cookie_file=cookie_file, cookie=cookie, headers=headers)

    out_mkv = (store.root_dir / "out" / f"{base}.url.mkv") if out is None else Path(out)

    subs = []
    want = [
        (f"{base}.spa.srt", "spa", "Spanish"),
        (f"{base}.spa.ru_a1plus.srt", "spa", "Spanish (RU A1+)"),
        (f"{base}.spa.ru_a2plus.srt", "spa", "Spanish (RU A2+)"),
        (f"{base}.spa.ru_b1plus.srt", "spa", "Spanish (RU B1+)"),
        (f"{base}.rus.srt", "rus", "Russian"),
        (f"{base}.eng.srt", "eng", "English"),
    ]
    for fname, lang, title in want:
        pth = tmp_dir / fname
        if not pth.exists():
            continue
        if fname.endswith(".rus.srt") and not (with_ru or require_ru):
            continue
        subs.append((pth, lang, title))

    mux_mkv(video_path=mp4_path, out_mkv=out_mkv, subs=subs)
    print(out_mkv)
    return 0
