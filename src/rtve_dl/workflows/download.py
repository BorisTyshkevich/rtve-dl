from __future__ import annotations

import re
from pathlib import Path

from rtve_dl.ffmpeg import download_to_mp4, mux_mkv
from rtve_dl.lexicon.store import SeriesStore
from rtve_dl.rtve.resolve import RtveResolver
from rtve_dl.subs.augment import augment_cues_with_glosses
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.vtt import parse_vtt
from rtve_dl.http import HttpClient
from rtve_dl.rtve.catalog import _extract_program_id_from_html, _iter_program_videos


_SEL_RE = re.compile(r"^T(?P<t>\d+)(?:S(?P<s>\d+))?$", re.IGNORECASE)


def _slug_title(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:80] if s else "episode"


def _pick_video_url(urls: list[str], quality: str, *, prefer_mp4: bool) -> str:
    if quality == "m3u8":
        for u in urls:
            if ".m3u8" in u:
                return u
    if quality == "mp4":
        for u in urls:
            if u.endswith(".mp4") or ".mp4?" in u:
                return u
    if prefer_mp4:
        for u in urls:
            if "rtve-mediavod-lote3.rtve.es" in u and ".mp4" in u:
                return u
        for u in urls:
            if ".mp4" in u:
                return u
    # best heuristic: prefer m3u8 master, then any m3u8, then mp4.
    for u in urls:
        if u.endswith(".m3u8") and "video.m3u8" in u:
            return u
    for u in urls:
        if ".m3u8" in u:
            return u
    return urls[0]


def _ensure_selector_in_db(store: SeriesStore, season: int, episode: int | None) -> None:
    """
    Ensure that at least the requested assets are present in the DB.

    This avoids having to index the entire series before testing a single episode.
    """
    con = store.connect()
    try:
        series_url = con.execute("SELECT value FROM meta WHERE key='series_url'").fetchone()["value"]
        if episode is None:
            row = con.execute("SELECT 1 FROM assets WHERE season=? LIMIT 1", (season,)).fetchone()
        else:
            row = con.execute("SELECT 1 FROM assets WHERE season=? AND episode=? LIMIT 1", (season, episode)).fetchone()
        if row is not None:
            return
    finally:
        con.close()

    http = HttpClient()
    html = http.get_text(series_url)
    program_id = _extract_program_id_from_html(html)
    if not program_id:
        return
    items = _iter_program_videos(program_id, http)

    # Insert only matching "Completo" items for requested selector.
    matches = []
    for it in items:
        t = (it.get("type") or {}).get("name")
        if t != "Completo":
            continue
        try:
            temp = int(it.get("temporadaOrden") or 0)
            ep = int(it.get("episode") or 0)
        except Exception:
            continue
        if temp != season:
            continue
        if episode is not None and ep != episode:
            continue
        if ep <= 0:
            continue
        matches.append(it)

    if not matches:
        return

    con = store.connect()
    try:
        for it in matches:
            con.execute(
                """
                INSERT OR REPLACE INTO assets(asset_id, episode_url, title, season, episode, has_drm, subtitles_es_url, subtitles_en_url)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(it.get("id")),
                    it.get("htmlUrl"),
                    it.get("title") or it.get("longTitle") or it.get("shortTitle"),
                    int(it.get("temporadaOrden")),
                    int(it.get("episode")),
                    1 if it.get("hasDRM") else 0,
                    "",
                    "",
                ),
            )
        con.commit()
    finally:
        con.close()


def download_selector(
    series_slug: str,
    selector: str,
    quality: str,
    *,
    with_ru: bool,
    require_ru: bool,
    ignore_drm: bool,
) -> int:
    m = _SEL_RE.match(selector.strip())
    if not m:
        raise SystemExit("selector must look like T7 or T7S5")
    season = int(m.group("t"))
    episode = int(m.group("s")) if m.group("s") else None

    store = SeriesStore.open_existing(series_slug)
    _ensure_selector_in_db(store, season, episode)
    con = store.connect()
    try:
        if episode is None:
            rows = con.execute(
                "SELECT asset_id, title, season, episode, has_drm, subtitles_es_url, subtitles_en_url FROM assets WHERE season=? ORDER BY episode",
                (season,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT asset_id, title, season, episode, has_drm, subtitles_es_url, subtitles_en_url FROM assets WHERE season=? AND episode=?",
                (season, episode),
            ).fetchall()
    finally:
        con.close()

    if not rows:
        raise SystemExit("no matching assets in cache; run `rtve_dl index <series_url>` first")

    resolver = RtveResolver()
    out_dir = store.root_dir / "out"
    tmp_dir = store.root_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    subs_dir = store.root_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)

    for r in rows:
        aid = r["asset_id"]
        if r["has_drm"] and not ignore_drm:
            raise SystemExit(
                f"DRM asset encountered: {aid} ({r['title'] or ''}). "
                "This project does not support DRM downloads; you can still use `rtve_dl subs` "
                "and then `rtve_dl mux-local` with your own legal video file."
            )
        resolved = resolver.resolve(aid, ignore_drm=ignore_drm)

        title = r["title"] or resolved.title or aid
        season_num = r["season"] or season
        episode_num = r["episode"] or (episode if episode is not None else 0)
        base = f"S{season_num:02d}E{episode_num:02d}_{_slug_title(title)}"

        out_mkv = out_dir / f"{base}.mkv"
        if out_mkv.exists():
            print(out_mkv)
            continue

        video_url = _pick_video_url(resolved.video_urls, quality, prefer_mp4=bool(r["has_drm"]) and ignore_drm)
        mp4_path = tmp_dir / f"{base}.mp4"
        if not mp4_path.exists():
            headers = {"Referer": "https://www.rtve.es/"}
            download_to_mp4(video_url, mp4_path, headers=headers)

        # Load ES cues from local VTT (downloaded by index); fallback to fetching now.
        es_vtt_path = subs_dir / f"{aid}.es.vtt"
        if not es_vtt_path.exists() and resolved.subtitles_es_vtt:
            from rtve_dl.http import HttpClient

            HttpClient().get_bytes(resolved.subtitles_es_vtt)  # preflight
            es_vtt_path.write_text(HttpClient().get_text(resolved.subtitles_es_vtt), encoding="utf-8")
        if not es_vtt_path.exists():
            raise SystemExit(f"missing ES subtitles for asset {aid}")

        es_cues = parse_vtt(es_vtt_path.read_text(encoding="utf-8"))

        con = store.connect()
        try:
            a1 = augment_cues_with_glosses(con, es_cues, "A1+")
            a2 = augment_cues_with_glosses(con, es_cues, "A2+")
            b1 = augment_cues_with_glosses(con, es_cues, "B1+")
        finally:
            con.close()

        srt_es = tmp_dir / f"{base}.spa.srt"
        srt_a1 = tmp_dir / f"{base}.spa.ru_a1plus.srt"
        srt_a2 = tmp_dir / f"{base}.spa.ru_a2plus.srt"
        srt_b1 = tmp_dir / f"{base}.spa.ru_b1plus.srt"
        srt_es.write_text(cues_to_srt(es_cues), encoding="utf-8")
        srt_a1.write_text(cues_to_srt(a1), encoding="utf-8")
        srt_a2.write_text(cues_to_srt(a2), encoding="utf-8")
        srt_b1.write_text(cues_to_srt(b1), encoding="utf-8")

        # EN subtitles if available (downloaded by index or resolve).
        srt_en_path = None
        en_vtt_path = subs_dir / f"{aid}.en.vtt"
        if not en_vtt_path.exists() and resolved.subtitles_en_vtt:
            from rtve_dl.http import HttpClient

            en_vtt_path.write_text(HttpClient().get_text(resolved.subtitles_en_vtt), encoding="utf-8")

        if en_vtt_path.exists():
            en_cues = parse_vtt(en_vtt_path.read_text(encoding="utf-8"))
            srt_en_path = tmp_dir / f"{base}.eng.srt"
            srt_en_path.write_text(cues_to_srt(en_cues), encoding="utf-8")

        subs = [
            (srt_es, "spa", "Spanish"),
            (srt_a1, "spa", "Spanish (RU A1+)"),
            (srt_a2, "spa", "Spanish (RU A2+)"),
            (srt_b1, "spa", "Spanish (RU B1+)"),
        ]
        if srt_en_path is not None:
            subs.append((srt_en_path, "eng", "English"))

        if with_ru:
            # RU full translation: require all ES cues translated if require_ru.
            con = store.connect()
            try:
                ru_rows = con.execute(
                    "SELECT cue_id, ru FROM ru_cues WHERE cue_id LIKE ? ORDER BY cue_id",
                    (f"{aid}:es:%",),
                ).fetchall()
                ru_map = {x["cue_id"]: x["ru"] for x in ru_rows}
            finally:
                con.close()

            ru_lines = []
            missing = 0
            for idx, c in enumerate(es_cues):
                cue_id = f"{aid}:es:{idx}"
                ru = ru_map.get(cue_id, "").strip()
                if not ru:
                    missing += 1
                    ru = ""  # keep blank, but count
                ru_lines.append((c.start_ms, c.end_ms, ru))

            if require_ru and missing:
                raise SystemExit(
                    f"RU translation missing for {missing} cues in asset {aid}. Run export-ru/import-ru."
                )

            from rtve_dl.subs.vtt import Cue

            ru_cues = [Cue(start_ms=a, end_ms=b, text=t) for (a, b, t) in ru_lines]
            srt_ru = tmp_dir / f"{base}.rus.srt"
            srt_ru.write_text(cues_to_srt(ru_cues), encoding="utf-8")
            subs.append((srt_ru, "rus", "Russian"))

        mux_mkv(video_path=mp4_path, out_mkv=out_mkv, subs=subs)

        print(out_mkv)

    return 0
