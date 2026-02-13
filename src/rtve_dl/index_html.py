from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from html import escape, unescape
import json
import re
from pathlib import Path
from urllib.parse import quote

from rtve_dl.codex_ru import translate_es_to_ru_with_codex
from rtve_dl.log import debug, error


_SEASON_EPISODE_RE = re.compile(r"S(\d{1,3})E(\d{1,3})(?:_|$)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _Row:
    name: str
    size_bytes: int
    mtime: float
    season: int | None
    episode: int | None


@dataclass(frozen=True)
class _CardMeta:
    key: str
    row: _Row
    title_es: str
    description_es: str
    title_ru: str
    description_ru: str
    release_date: str
    image_url: str | None


def _parse_season_episode(name: str) -> tuple[int, int] | None:
    m = _SEASON_EPISODE_RE.search(name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    value = float(size_bytes)
    units = ["KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
    return f"{size_bytes} B"


def _format_size_gb(size_bytes: int) -> str:
    gb = float(size_bytes) / (1024.0 * 1024.0 * 1024.0)
    return f"{gb:.2f} GB"


def _format_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _clean_text(s: str | None) -> str:
    if not s:
        return ""
    t = unescape(s)
    t = _TAG_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def _normalize_release_date(s: str | None) -> str:
    # RTVE usually: "13-09-2007 00:00:00"
    if not s:
        return ""
    m = re.match(r"^\s*(\d{2})-(\d{2})-(\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}"
    return _clean_text(s)


def _mkv_rows(out_dir: Path) -> list[_Row]:
    rows: list[_Row] = []
    for p in sorted(out_dir.glob("*.mkv")):
        try:
            st = p.stat()
        except OSError:
            continue
        se = _parse_season_episode(p.name)
        rows.append(
            _Row(
                name=p.name,
                size_bytes=int(st.st_size),
                mtime=float(st.st_mtime),
                season=(se[0] if se else None),
                episode=(se[1] if se else None),
            )
        )

    def _key(r: _Row) -> tuple[int, int, int, str]:
        if r.season is not None and r.episode is not None:
            return (0, r.season, r.episode, r.name)
        return (1, 0, 0, r.name)

    rows.sort(key=_key)
    return rows


def _latest_catalog_items(tmp_dir: Path | None) -> list[dict]:
    if tmp_dir is None or not tmp_dir.exists():
        return []
    files = sorted(tmp_dir.glob("catalog_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = obj.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def _catalog_maps(items: list[dict]) -> tuple[dict[tuple[int, int], dict], dict[str, dict]]:
    by_se: dict[tuple[int, int], dict] = {}
    by_asset: dict[str, dict] = {}
    for it in items:
        t = (it.get("type") or {}).get("name")
        if t != "Completo":
            continue
        if (it.get("assetType") or it.get("contentType")) != "video":
            continue
        aid = str(it.get("id") or "").strip()
        if aid:
            by_asset[aid] = it
        try:
            season = int(it.get("temporadaOrden") or 0)
            episode = int(it.get("episode") or 0)
        except Exception:
            continue
        if season > 0 and episode > 0:
            by_se[(season, episode)] = it
    return by_se, by_asset


def _row_asset_id(row: _Row) -> str:
    m = re.search(r"/(\d+)\.mkv$", row.name)
    if m:
        return m.group(1)
    return ""


def _cache_file(tmp_dir: Path | None) -> Path | None:
    if tmp_dir is None:
        return None
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / "index_meta_ru.json"


def _load_ru_cache(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {"version": 1, "items": {}}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "items": {}}
    if not isinstance(obj, dict):
        return {"version": 1, "items": {}}
    items = obj.get("items")
    if not isinstance(items, dict):
        items = {}
    return {"version": 1, "items": items}


def _save_ru_cache(path: Path | None, cache: dict) -> None:
    if path is None:
        return
    tmp = Path(str(path) + ".partial")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _text_hash(s: str) -> str:
    return sha1(s.encode("utf-8")).hexdigest()


def _translate_ru_for_cards(
    cards: list[_CardMeta],
    *,
    tmp_dir: Path | None,
    codex_model: str | None,
    codex_chunk_cues: int,
    jobs_codex_chunks: int,
) -> dict[str, tuple[str, str]]:
    path = _cache_file(tmp_dir)
    cache = _load_ru_cache(path)
    cache_items: dict = cache["items"]

    cues: list[tuple[str, str]] = []
    out: dict[str, tuple[str, str]] = {}

    for c in cards:
        title_h = _text_hash(c.title_es)
        desc_h = _text_hash(c.description_es)
        prev = cache_items.get(c.key) if isinstance(cache_items.get(c.key), dict) else {}
        title_ru = str(prev.get("title_ru") or "")
        desc_ru = str(prev.get("description_ru") or "")

        if c.title_es and (prev.get("title_es_hash") != title_h or not title_ru):
            cues.append((f"{c.key}|title", c.title_es))
        if c.description_es and (prev.get("description_es_hash") != desc_h or not desc_ru):
            cues.append((f"{c.key}|desc", c.description_es))

        out[c.key] = (title_ru, desc_ru)

    if cues:
        try:
            base_path = (tmp_dir or Path("tmp")) / "index_meta_ru"
            ru_map = translate_es_to_ru_with_codex(
                cues=cues,
                base_path=base_path,
                chunk_size_cues=max(1, min(codex_chunk_cues, 50)),
                model=codex_model,
                resume=True,
                max_workers=max(1, jobs_codex_chunks),
            )
            for cue_id, tr in ru_map.items():
                if "|title" in cue_id:
                    key = cue_id.rsplit("|title", 1)[0]
                    t, d = out.get(key, ("", ""))
                    out[key] = (tr, d)
                elif "|desc" in cue_id:
                    key = cue_id.rsplit("|desc", 1)[0]
                    t, d = out.get(key, ("", ""))
                    out[key] = (t, tr)
        except Exception as e:
            error(f"index ru translation failed, continuing with ES only: {e}")

    # Persist cache with latest hashes and available translations.
    for c in cards:
        title_ru, desc_ru = out.get(c.key, ("", ""))
        cache_items[c.key] = {
            "title_es_hash": _text_hash(c.title_es),
            "description_es_hash": _text_hash(c.description_es),
            "title_ru": title_ru,
            "description_ru": desc_ru,
        }
    _save_ru_cache(path, cache)
    return out


def build_slug_index(
    out_dir: Path,
    *,
    tmp_dir: Path | None = None,
    codex_model: str | None = None,
    codex_chunk_cues: int = 400,
    jobs_codex_chunks: int = 4,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _mkv_rows(out_dir)
    total_size = sum(r.size_bytes for r in rows)

    items = _latest_catalog_items(tmp_dir)
    by_se, by_asset = _catalog_maps(items)
    cards: list[_CardMeta] = []

    for r in rows:
        it = None
        if r.season is not None and r.episode is not None:
            it = by_se.get((r.season, r.episode))
        aid = _row_asset_id(r)
        if it is None and aid:
            it = by_asset.get(aid)

        title_es = _clean_text((it or {}).get("title")) or _clean_text((it or {}).get("longTitle")) or _clean_text(Path(r.name).stem)
        description_es = _clean_text((it or {}).get("description"))
        release_date = _normalize_release_date((it or {}).get("dateOfEmission"))
        image_url = (it or {}).get("thumbnail") or None
        if not image_url:
            image_url = (it or {}).get("imageSEO") or None
        key = str((it or {}).get("id") or f"S{r.season or 0:02d}E{r.episode or 0:02d}:{Path(r.name).stem}")
        cards.append(
            _CardMeta(
                key=key,
                row=r,
                title_es=title_es,
                description_es=description_es,
                title_ru="",
                description_ru="",
                release_date=release_date,
                image_url=image_url,
            )
        )

    ru_map = _translate_ru_for_cards(
        cards,
        tmp_dir=tmp_dir,
        codex_model=codex_model,
        codex_chunk_cues=codex_chunk_cues,
        jobs_codex_chunks=jobs_codex_chunks,
    )

    body_rows: list[str] = []
    for c in cards:
        rel_href = quote(c.row.name)
        title_ru, desc_ru = ru_map.get(c.key, ("", ""))
        episode_id = (
            f"S{c.row.season:02d}E{c.row.episode:02d}"
            if c.row.season is not None and c.row.episode is not None
            else Path(c.row.name).stem
        )

        media_title = escape(Path(c.row.name).stem, quote=True)
        img_inner = (
            f"<img src=\"{escape(c.image_url, quote=True)}\" alt=\"{escape(c.title_es)}\" loading=\"lazy\" />"
            if c.image_url
            else "<div class=\"noimg\">No image</div>"
        )
        img_html = (
            f"<a href=\"#\" class=\"m3u-generate media-thumb\" data-media=\"{escape(rel_href, quote=True)}\" "
            f"data-title=\"{media_title}\">{img_inner}</a>"
        )

        epid_html = (
            f"<a href=\"#\" class=\"m3u-generate epid-link\" data-media=\"{escape(rel_href, quote=True)}\" "
            f"data-title=\"{media_title}\">{escape(episode_id)}</a>"
        )
        title_es_html = f"<div class=\"title-es\">{escape(c.title_es)}</div>"
        desc_es_html = escape(c.description_es) if c.description_es else "—"
        title_ru_html = f"<div class=\"title-ru\">{escape(title_ru)}</div>" if title_ru else "<div class=\"title-ru\">—</div>"
        desc_ru_html = escape(desc_ru) if desc_ru else "—"
        release_html = escape(c.release_date) if c.release_date else "—"
        size_html = _format_size_gb(c.row.size_bytes)

        body_rows.append(
            "<tr>"
            f"<td class=\"thumb col-meta\"><div class=\"epid-wrap\">{epid_html}</div>{img_html}"
            "<div class=\"meta-lines\">"
            f"<div>{release_html}</div>"
            f"<div>{escape(size_html)}"
            f" | <a href=\"{escape(rel_href, quote=True)}\" download>Download</a>"
            f" | <span class=\"dt\">{escape(_format_mtime(c.row.mtime))}</span>"
            "</div>"
            "</div>"
            "</td>"
            "<td class=\"col-es\">"
            f"{title_es_html}"
            f"<div class=\"desc\">{desc_es_html}</div>"
            "</td>"
            "<td class=\"col-ru\">"
            f"{title_ru_html}"
            f"<div class=\"desc\">{desc_ru_html}</div>"
            "</td>"
            "</tr>"
        )

    if not body_rows:
        body_rows.append("<tr><td colspan=\"3\" class=\"empty\">No MKV files found.</td></tr>")

    title = f"RTVE Downloads: {out_dir.name}"
    rows_html = "\n        ".join(body_rows)

    html = (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\" />\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        f"  <title>{escape(title)}</title>\n"
        "  <style>\n"
        "    :root { color-scheme: light; }\n"
        "    body { margin: 24px; font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif; color: #1f2937; }\n"
        "    h1 { margin: 0 0 8px; font-size: 1.5rem; }\n"
        "    .meta-top { margin: 0 0 16px; color: #4b5563; }\n"
        "    .wrap { overflow-x: auto; border: 1px solid #e5e7eb; border-radius: 10px; }\n"
        "    table { width: 100%; border-collapse: collapse; min-width: 1080px; }\n"
        "    th, td { padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; }\n"
        "    th { position: sticky; top: 0; background: #f8fafc; font-weight: 600; }\n"
        "    tbody tr:nth-child(even) { background: #fcfcfd; }\n"
        "    tbody tr:hover { background: #f5faff; }\n"
        "    .thumb { width: 260px; }\n"
        "    .thumb img { width: 240px; max-width: 240px; height: auto; border-radius: 8px; display: block; background: #eef2f7; }\n"
        "    .noimg { width: 240px; height: 135px; border-radius: 8px; background: #eef2f7; color: #6b7280; display: flex; align-items: center; justify-content: center; }\n"
        "    .col-meta { width: 300px; }\n"
        "    .epid-wrap { display: block; margin-top: 8px; }\n"
        "    .epid-link { display: inline-block; font-size: 0.95rem; font-weight: 700; color: #111827; text-decoration: none; }\n"
        "    .epid-link:hover { text-decoration: underline; }\n"
        "    .media-thumb { display: block; text-decoration: none; }\n"
        "    .meta-lines { margin-top: 8px; color: #374151; }\n"
        "    .meta-lines div { margin: 0 0 6px; line-height: 1.45; }\n"
        "    .col-es, .col-ru { width: 390px; }\n"
        "    .title-es, .title-ru { display: block; font-size: 1.08rem; font-weight: 600; line-height: 1.35; margin: 0 0 8px; }\n"
        "    .desc { color: #374151; line-height: 1.45; white-space: pre-wrap; }\n"
        "    .dt { color: #6b7280; }\n"
        "    .empty { color: #6b7280; font-style: italic; }\n"
        "    a { color: #0b57d0; text-decoration: none; }\n"
        "    a:hover { text-decoration: underline; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"  <h1>{escape(title)}</h1>\n"
        f"  <p class=\"meta-top\">Files: {len(rows)} | Total size: {escape(_format_size(total_size))}</p>\n"
        "  <div class=\"wrap\">\n"
        "    <table>\n"
        "      <tbody>\n"
        f"        {rows_html}\n"
        "      </tbody>\n"
        "    </table>\n"
        "  </div>\n"
        "  <script>\n"
        "    (function () {\n"
        "      var LAUNCH_TIMEOUT_MS = 1600;\n"
        "      function launchVLC(absUrl, onFallback) {\n"
        "        var done = false;\n"
        "        var timer = null;\n"
        "        function cleanup() {\n"
        "          if (timer) clearTimeout(timer);\n"
        "          document.removeEventListener('visibilitychange', onVisibility);\n"
        "        }\n"
        "        function onVisibility() {\n"
        "          if (document.visibilityState === 'hidden') {\n"
        "            done = true;\n"
        "            cleanup();\n"
        "          }\n"
        "        }\n"
        "        document.addEventListener('visibilitychange', onVisibility);\n"
        "        timer = setTimeout(function () {\n"
        "          if (done) return;\n"
        "          cleanup();\n"
        "          onFallback();\n"
        "        }, LAUNCH_TIMEOUT_MS);\n"
        "        window.location.href = 'vlc://' + absUrl;\n"
        "      }\n"
        "      function downloadM3U(mediaRel, title) {\n"
        "        var absUrl = new URL(mediaRel, window.location.href).href;\n"
        "        var safeTitle = (title || 'playlist').replace(/[\\\\/]/g, '_');\n"
        "        var m3u = '#EXTM3U\\n#EXTINF:-1,' + safeTitle + '\\n' + absUrl + '\\n';\n"
        "        var blob = new Blob([m3u], { type: 'audio/x-mpegurl' });\n"
        "        var blobUrl = URL.createObjectURL(blob);\n"
        "        var a = document.createElement('a');\n"
        "        a.href = blobUrl;\n"
        "        a.download = safeTitle + '.m3u';\n"
        "        document.body.appendChild(a);\n"
        "        a.click();\n"
        "        a.remove();\n"
        "        setTimeout(function () { URL.revokeObjectURL(blobUrl); }, 1000);\n"
        "      }\n"
        "      var links = document.querySelectorAll('a.m3u-generate[data-media]');\n"
        "      for (var i = 0; i < links.length; i++) {\n"
        "        links[i].addEventListener('click', function (ev) {\n"
        "          ev.preventDefault();\n"
        "          var mediaRel = this.getAttribute('data-media');\n"
        "          var title = this.getAttribute('data-title') || this.textContent || 'playlist';\n"
        "          if (!mediaRel) return;\n"
        "          var absUrl = new URL(mediaRel, window.location.href).href;\n"
        "          launchVLC(absUrl, function () { downloadM3U(mediaRel, title); });\n"
        "        });\n"
        "      }\n"
        "    })();\n"
        "  </script>\n"
        "</body>\n"
        "</html>\n"
    )

    index_path = out_dir / "index.html"
    tmp_path = out_dir / "index.html.partial"
    tmp_path.write_text(html, encoding="utf-8")
    tmp_path.replace(index_path)
    debug(f"index metadata: rows={len(rows)} cards={len(cards)} catalog_items={len(items)}")
    return index_path
