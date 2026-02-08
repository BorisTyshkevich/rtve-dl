from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rtve_dl.lexicon.store import SeriesStore
from rtve_dl.subs.augment import augment_cues_with_glosses
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.vtt import Cue, parse_vtt
from rtve_dl.http import HttpClient
from rtve_dl.rtve.catalog import _extract_program_id_from_html, _iter_program_videos
from rtve_dl.rtve.api import RtveApi
from rtve_dl.subs.terms import extract_words


_SEL_RE = re.compile(r"^T(?P<t>\d+)(?:S(?P<s>\d+))?$", re.IGNORECASE)


@dataclass(frozen=True)
class BuiltSubs:
    base: str
    srt_paths: list[tuple[Path, str, str]]  # (path, language, title)


def _slug_title(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:80] if s else "episode"


def _get_assets_for_selector(store: SeriesStore, selector: str):
    m = _SEL_RE.match(selector.strip())
    if not m:
        raise SystemExit("selector must look like T7 or T7S5")
    season = int(m.group("t"))
    episode = int(m.group("s")) if m.group("s") else None
    con = store.connect()
    try:
        if episode is None:
            rows = con.execute(
                "SELECT asset_id, title, season, episode, subtitles_es_url, subtitles_en_url FROM assets WHERE season=? ORDER BY episode",
                (season,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT asset_id, title, season, episode, subtitles_es_url, subtitles_en_url FROM assets WHERE season=? AND episode=?",
                (season, episode),
            ).fetchall()
    finally:
        con.close()
    if not rows:
        raise SystemExit("no matching assets in cache; run `rtve_dl index <series_url>` first")
    return rows


def _ensure_selector_in_db(store: SeriesStore, season: int, episode: int | None) -> None:
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
    api = RtveApi(http)
    html = http.get_text(series_url)
    program_id = _extract_program_id_from_html(html)
    if not program_id:
        return
    items = _iter_program_videos(program_id, http)

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


def build_subtitles_for_selector(
    series_slug: str,
    selector: str,
    *,
    with_ru: bool,
    require_ru: bool,
) -> list[Path]:
    store = SeriesStore.open_existing(series_slug)
    m = _SEL_RE.match(selector.strip())
    if not m:
        raise SystemExit("selector must look like T7 or T7S5")
    season = int(m.group("t"))
    episode = int(m.group("s")) if m.group("s") else None
    _ensure_selector_in_db(store, season, episode)
    rows = _get_assets_for_selector(store, selector)

    tmp_dir = store.root_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    subs_dir = store.root_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)

    out_srt_paths: list[Path] = []
    http = HttpClient()
    api = RtveApi(http)
    stop = store.load_stopwords()

    for r in rows:
        aid = r["asset_id"]
        title = r["title"] or aid
        season_num = int(r["season"] or 0)
        episode_num = int(r["episode"] or 0)
        base = f"S{season_num:02d}E{episode_num:02d}_{_slug_title(title)}"

        es_vtt_path = subs_dir / f"{aid}.es.vtt"
        subs_es_url = r["subtitles_es_url"] or ""
        subs_en_url = r["subtitles_en_url"] or ""
        if (not subs_es_url or not subs_en_url) and (not es_vtt_path.exists()):
            # Populate missing subtitle URLs on-demand (common if assets were inserted lazily).
            subs = api.get_subtitles(aid)
            for s in subs:
                lang = (s.get("lang") or "").lower()
                src = s.get("src")
                if not isinstance(src, str) or not src:
                    continue
                if lang == "es" and not subs_es_url:
                    subs_es_url = src
                if lang in ("en", "eng") and not subs_en_url:
                    subs_en_url = src
        if not es_vtt_path.exists() and subs_es_url:
            es_vtt_path.write_text(http.get_text(subs_es_url), encoding="utf-8")
        if not es_vtt_path.exists():
            raise SystemExit(f"missing ES subtitles for asset {aid}")

        es_cues = parse_vtt(es_vtt_path.read_text(encoding="utf-8"))

        # Persist cues/terms if not already present (keeps export-ru working even if you didn't index the whole series).
        con = store.connect()
        try:
            already = con.execute("SELECT 1 FROM cues WHERE asset_id=? AND lang='es' LIMIT 1", (aid,)).fetchone()
            if already is None:
                import json as _json

                for idx, cue in enumerate(es_cues):
                    cue_id = f"{aid}:es:{idx}"
                    con.execute(
                        "INSERT OR REPLACE INTO cues(cue_id, asset_id, lang, idx, start_ms, end_ms, text) VALUES(?,?,?,?,?,?,?)",
                        (cue_id, aid, "es", idx, cue.start_ms, cue.end_ms, cue.text),
                    )
                    tokens = [t for t in extract_words(cue.text) if t and t not in stop]
                    ctx = _json.dumps([cue.text], ensure_ascii=False)
                    for w in tokens:
                        con.execute(
                            """
                            INSERT INTO terms(term, kind, count, contexts_json)
                            VALUES(?, 'word', 1, ?)
                            ON CONFLICT(term, kind) DO UPDATE SET
                              count = count + 1
                            """,
                            (w, ctx),
                        )
                con.commit()
        finally:
            con.close()

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
        if not srt_es.exists():
            srt_es.write_text(cues_to_srt(es_cues), encoding="utf-8")
        srt_a1.write_text(cues_to_srt(a1), encoding="utf-8")
        srt_a2.write_text(cues_to_srt(a2), encoding="utf-8")
        srt_b1.write_text(cues_to_srt(b1), encoding="utf-8")
        out_srt_paths += [srt_es, srt_a1, srt_a2, srt_b1]

        en_vtt_path = subs_dir / f"{aid}.en.vtt"
        if not en_vtt_path.exists() and subs_en_url:
            en_vtt_path.write_text(http.get_text(subs_en_url), encoding="utf-8")
        if en_vtt_path.exists():
            srt_en = tmp_dir / f"{base}.eng.srt"
            if not srt_en.exists():
                en_cues = parse_vtt(en_vtt_path.read_text(encoding="utf-8"))
                srt_en.write_text(cues_to_srt(en_cues), encoding="utf-8")
            out_srt_paths.append(srt_en)

        if with_ru:
            con = store.connect()
            try:
                ru_rows = con.execute(
                    "SELECT cue_id, ru FROM ru_cues WHERE cue_id LIKE ? ORDER BY cue_id",
                    (f"{aid}:es:%",),
                ).fetchall()
                ru_map = {x["cue_id"]: x["ru"] for x in ru_rows}
            finally:
                con.close()

            missing = 0
            ru_cues: list[Cue] = []
            for idx, c in enumerate(es_cues):
                cue_id = f"{aid}:es:{idx}"
                ru = (ru_map.get(cue_id, "") or "").strip()
                if not ru:
                    missing += 1
                ru_cues.append(Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=ru))
            if require_ru and missing:
                raise SystemExit(f"RU translation missing for {missing} cues in asset {aid}. Run export-ru/import-ru.")
            srt_ru = tmp_dir / f"{base}.rus.srt"
            srt_ru.write_text(cues_to_srt(ru_cues), encoding="utf-8")
            out_srt_paths.append(srt_ru)

    return out_srt_paths
