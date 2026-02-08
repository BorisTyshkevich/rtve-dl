from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rtve_dl.http import HttpClient
from rtve_dl.lexicon.store import SeriesStore
from rtve_dl.rtve.api import RtveApi
from rtve_dl.subs.terms import extract_words
from rtve_dl.subs.vtt import parse_vtt


@dataclass(frozen=True)
class SeriesAsset:
    asset_id: str
    episode_url: str | None
    title: str | None
    season: int | None
    episode: int | None
    has_drm: bool


_ASSET_ID_RE = re.compile(r"/(\d{4,})/")
_PROGRAM_ID_RE = re.compile(r"/api/programas/(\d+)/")
_SEL_RE = re.compile(r"^T(?P<t>\d+)(?:S(?P<s>\d+))?$", re.IGNORECASE)


def _extract_asset_ids_from_html(series_html: str) -> list[str]:
    # Heuristic: find numeric /<id>/ in links. De-dupe while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for m in _ASSET_ID_RE.finditer(series_html):
        aid = m.group(1)
        if aid not in seen:
            seen.add(aid)
            out.append(aid)
    return out


def _extract_program_id_from_html(series_html: str) -> str | None:
    m = _PROGRAM_ID_RE.search(series_html)
    return m.group(1) if m else None


def _iter_program_videos(program_id: str, http: HttpClient) -> list[dict]:
    # Paginated feed; size=60 is accepted and keeps requests bounded.
    size = 60
    page = 1
    items: list[dict] = []
    while True:
        url = f"https://www.rtve.es/api/programas/{program_id}/videos.json?size={size}&page={page}"
        data = http.get_json(url)
        p = data.get("page", {})
        its = p.get("items", [])
        if isinstance(its, list):
            items.extend([x for x in its if isinstance(x, dict)])
        total_pages = int(p.get("totalPages") or 1)
        if page >= total_pages:
            break
        page += 1
    return items


def _parse_selector(selector: str) -> tuple[int, int | None]:
    m = _SEL_RE.match(selector.strip())
    if not m:
        raise SystemExit("selector must look like T7 or T7S5")
    season = int(m.group("t"))
    episode = int(m.group("s")) if m.group("s") else None
    return season, episode


def build_series_index(store: SeriesStore, max_assets: int | None = None, selector: str | None = None) -> None:
    con = store.connect()
    try:
        series_url = con.execute("SELECT value FROM meta WHERE key='series_url'").fetchone()["value"]
    finally:
        con.close()

    http = HttpClient()
    api = RtveApi(http)

    want_season: int | None = None
    want_episode: int | None = None
    if selector:
        want_season, want_episode = _parse_selector(selector)

    html = http.get_text(series_url)
    program_id = _extract_program_id_from_html(html)

    assets: list[SeriesAsset] = []
    if program_id:
        items = _iter_program_videos(program_id, http)
        for it in items:
            t = (it.get("type") or {}).get("name")
            if t != "Completo":
                continue
            if (it.get("assetType") or it.get("contentType")) != "video":
                continue
            temporada = it.get("temporadaOrden")
            episode = it.get("episode")
            if temporada is None or episode is None:
                continue
            try:
                temporada_i = int(temporada)
                episode_i = int(episode)
            except Exception:
                continue
            if episode_i <= 0:
                continue
            if want_season is not None and temporada_i != want_season:
                continue
            if want_episode is not None and episode_i != want_episode:
                continue
            assets.append(
                SeriesAsset(
                    asset_id=str(it.get("id")),
                    episode_url=it.get("htmlUrl"),
                    title=it.get("title") or it.get("longTitle") or it.get("shortTitle"),
                    season=temporada_i,
                    episode=episode_i,
                    has_drm=bool(it.get("hasDRM") or False),
                )
            )
    else:
        # Fallback: best-effort from HTML, then probe metadata APIs.
        asset_ids = _extract_asset_ids_from_html(html)
        if max_assets is not None:
            asset_ids = asset_ids[: max_assets]
        for aid in asset_ids:
            try:
                meta = api.get_video_meta(aid)
            except Exception:
                continue
            if want_season is not None and meta.season is not None and meta.season != want_season:
                continue
            if want_episode is not None and meta.episode is not None and meta.episode != want_episode:
                continue
            assets.append(
                SeriesAsset(
                    asset_id=aid,
                    episode_url=None,
                    title=meta.title,
                    season=meta.season,
                    episode=meta.episode,
                    has_drm=meta.has_drm,
                )
            )

    if max_assets is not None:
        assets = assets[: max_assets]

    con = store.connect()
    try:
        stop = store.load_stopwords()
        for a in assets:
            aid = a.asset_id
            # For program feed entries, meta API is optional; keep it best-effort.
            meta = None
            try:
                meta = api.get_video_meta(aid)
            except Exception:
                meta = None
            subs = api.get_subtitles(aid)
            es = None
            en = None
            for s in subs:
                lang = (s.get("lang") or "").lower()
                src = s.get("src")
                if not isinstance(src, str) or not src:
                    continue
                if lang == "es" and es is None:
                    es = src
                if lang in ("en", "eng") and en is None:
                    en = src

            con.execute(
                """
                INSERT OR REPLACE INTO assets(asset_id, episode_url, title, season, episode, has_drm, subtitles_es_url, subtitles_en_url)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aid,
                    a.episode_url,
                    (meta.title if meta else a.title),
                    (meta.season if meta and meta.season is not None else a.season),
                    (meta.episode if meta and meta.episode is not None else a.episode),
                    1 if (meta.has_drm if meta else a.has_drm) else 0,
                    es,
                    en,
                ),
            )

            # If we've already indexed cues for this asset, skip downloading/parsing again to avoid
            # double-counting term frequencies on repeated `index` runs.
            already_indexed = (
                con.execute("SELECT 1 FROM cues WHERE asset_id=? AND lang='es' LIMIT 1", (aid,)).fetchone()
                is not None
            )
            if already_indexed:
                continue

            # Download and parse ES/EN subtitles for term dataset.
            for lang, url in (("es", es), ("en", en)):
                if not url:
                    continue
                vtt_path = store.root_dir / "subs"
                vtt_path.mkdir(parents=True, exist_ok=True)
                local_vtt = vtt_path / f"{aid}.{lang}.vtt"
                if not local_vtt.exists():
                    b = http.get_text(url)
                    local_vtt.write_text(b, encoding="utf-8")
                cues = parse_vtt(local_vtt.read_text(encoding="utf-8"))
                # Persist cues for later full-RU translation export (based on ES only).
                for idx, cue in enumerate(cues):
                    cue_id = f"{aid}:{lang}:{idx}"
                    con.execute(
                        "INSERT OR REPLACE INTO cues(cue_id, asset_id, lang, idx, start_ms, end_ms, text) VALUES(?,?,?,?,?,?,?)",
                        (cue_id, aid, lang, idx, cue.start_ms, cue.end_ms, cue.text),
                    )
                if lang != "es":
                    continue
                # Terms only from Spanish (words only). Phrase mining is done separately to avoid exploding
                # the dataset by inserting all possible n-grams during indexing.
                for cue in cues:
                    tokens = [t for t in extract_words(cue.text) if t and t not in stop]
                    ctx = json.dumps([cue.text], ensure_ascii=False)
                    # Words
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
