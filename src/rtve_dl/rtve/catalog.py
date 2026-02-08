from __future__ import annotations

import re
from dataclasses import dataclass

from rtve_dl.http import HttpClient


@dataclass(frozen=True)
class SeriesAsset:
    asset_id: str
    episode_url: str | None
    title: str | None
    season: int | None
    episode: int | None
    has_drm: bool


_PROGRAM_ID_RE = re.compile(r"/api/programas/(\d+)/")
_SEL_RE = re.compile(r"^T(?P<t>\d+)(?:S(?P<s>\d+))?$", re.IGNORECASE)


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


def list_assets_for_selector(series_url: str, selector: str, http: HttpClient | None = None) -> list[SeriesAsset]:
    """
    Resolve season/episode selection into a list of RTVE assets using RTVE's public program feed.
    """
    http = http or HttpClient()
    season, episode = parse_selector(selector)

    html = http.get_text(series_url)
    program_id = extract_program_id_from_html(html)
    if not program_id:
        raise SystemExit("could not find program id on series page")

    items = iter_program_videos(program_id, http)

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
                title=it.get("title") or it.get("longTitle") or it.get("shortTitle"),
                season=temp,
                episode=ep,
                has_drm=bool(it.get("hasDRM") or False),
            )
        )

    assets.sort(key=lambda a: (a.season or 0, a.episode or 0, a.asset_id))
    if not assets:
        raise SystemExit("no matching assets found for selector (season/episode)")
    return assets

