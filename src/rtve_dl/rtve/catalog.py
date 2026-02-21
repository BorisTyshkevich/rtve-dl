from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from rtve_dl.http import HttpClient
from rtve_dl.log import debug
from rtve_dl.rtve.constants import CATALOG_CACHE_TTL_S


@dataclass(frozen=True)
class SeriesAsset:
    asset_id: str
    episode_url: str | None
    title: str | None
    short_description: str | None
    description: str | None
    season: int | None
    episode: int | None
    has_drm: bool


_PROGRAM_ID_RE = re.compile(r"/api/programas/(\d+)/")
_SEL_RE = re.compile(r"^T(?P<t>\d+)(?:S(?P<s>\d+))?$", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(s: str | None) -> str:
    if not s:
        return ""
    t = unescape(s)
    t = _TAG_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def extract_program_id_from_html(series_html: str) -> str | None:
    m = _PROGRAM_ID_RE.search(series_html)
    return m.group(1) if m else None


def parse_selector(selector: str) -> tuple[int, int | None]:
    m = _SEL_RE.match(selector.strip())
    if not m:
        raise SystemExit("selector must look like T7 or T7S5")
    season = int(m.group("t"))
    episode = int(m.group("s")) if m.group("s") else None
    return season, episode


def iter_program_videos(program_id: str, http: HttpClient) -> list[dict]:
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


def _catalog_cache_path(series_url: str, cache_dir: Path) -> Path:
    key = hashlib.sha1(series_url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"catalog_{key}.json"


def _read_catalog_cache(path: Path, *, ttl_s: int) -> dict | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    fetched_at = int(obj.get("fetched_at") or 0)
    if fetched_at <= 0:
        return None
    age = int(time.time()) - fetched_at
    if age > ttl_s:
        debug(f"catalog cache stale: {path} age={age}s ttl={ttl_s}s")
        return None
    if not isinstance(obj.get("items"), list):
        return None
    debug(f"catalog cache hit: {path} age={age}s")
    return obj


def _write_catalog_cache(path: Path, *, series_url: str, program_id: str, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "series_url": series_url,
        "program_id": program_id,
        "fetched_at": int(time.time()),
        "items": items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def list_assets_for_selector(
    series_url: str,
    selector: str,
    http: HttpClient | None = None,
    *,
    cache_dir: Path | None = None,
) -> list[SeriesAsset]:
    """
    Resolve season/episode selection into a list of RTVE assets using RTVE's public program feed.
    """
    http = http or HttpClient()
    season, episode = parse_selector(selector)

    cache_path: Path | None = _catalog_cache_path(series_url, cache_dir) if cache_dir is not None else None
    cached: dict | None = _read_catalog_cache(cache_path, ttl_s=CATALOG_CACHE_TTL_S) if cache_path else None
    if cached is not None:
        program_id = str(cached.get("program_id") or "")
        items = cached.get("items", [])
    else:
        html = http.get_text(series_url)
        program_id = extract_program_id_from_html(html)
        if not program_id:
            raise SystemExit("could not find program id on series page")
        items = iter_program_videos(program_id, http)
        if cache_path is not None:
            _write_catalog_cache(cache_path, series_url=series_url, program_id=program_id, items=items)

    assets: list[SeriesAsset] = []
    for it in items:
        t = (it.get("type") or {}).get("name")
        if t != "Completo":
            continue
        if (it.get("assetType") or it.get("contentType")) != "video":
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
        assets.append(
            SeriesAsset(
                asset_id=str(it.get("id")),
                episode_url=it.get("htmlUrl"),
                title=_clean_text(it.get("title") or it.get("longTitle") or it.get("shortTitle")) or None,
                short_description=_clean_text(it.get("shortDescription")) or None,
                description=_clean_text(it.get("description")) or None,
                season=temp,
                episode=ep,
                has_drm=bool(it.get("hasDRM") or False),
            )
        )

    assets.sort(key=lambda a: (a.season or 0, a.episode or 0, a.asset_id))
    if not assets:
        raise SystemExit("no matching assets found for selector (season/episode)")
    return assets
