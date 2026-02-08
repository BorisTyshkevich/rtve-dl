from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rtve_dl.http import HttpClient


@dataclass(frozen=True)
class VideoMeta:
    asset_id: str
    title: str | None
    season: int | None
    episode: int | None
    has_drm: bool
    program_id: str | None
    program_title: str | None
    program_url: str | None


class RtveApi:
    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient()

    def get_video_meta(self, asset_id: str) -> VideoMeta:
        # Seen in Descargavideos comments: https://api-ztnr.rtve.es/api/videos/<id>.json
        url = f"https://api-ztnr.rtve.es/api/videos/{asset_id}.json"
        data = self._http.get_json(url)
        # This endpoint typically returns {"page":{"items":[{...}]...}}
        item = None
        if isinstance(data, dict) and "page" in data and data["page"].get("items"):
            item = data["page"]["items"][0]
        elif isinstance(data, dict) and "id" in data:
            item = data
        if not isinstance(item, dict):
            raise RuntimeError(f"unexpected meta payload for {asset_id}")

        title = item.get("title") or item.get("longTitle") or item.get("shortTitle")
        season = item.get("temporadaOrden") or (item.get("temporada") or {}).get("orden")
        episode = item.get("episode") or item.get("capitulo")
        has_drm = bool(item.get("hasDRM") or item.get("drm") or False)

        prog = item.get("programInfo") or {}
        program_id = prog.get("id") or None
        program_title = prog.get("title") or None
        program_url = prog.get("htmlUrl") or None

        def _to_int(x: Any) -> int | None:
            try:
                return int(x)
            except Exception:
                return None

        return VideoMeta(
            asset_id=str(item.get("id") or asset_id),
            title=title,
            season=_to_int(season),
            episode=_to_int(episode),
            has_drm=has_drm,
            program_id=str(program_id) if program_id is not None else None,
            program_title=program_title,
            program_url=program_url,
        )

    def get_subtitles(self, asset_id: str) -> list[dict[str, Any]]:
        # Prefer api2, fallback to api1.
        urls = [
            f"https://api2.rtve.es/api/videos/{asset_id}/subtitulos.json",
            f"https://www.rtve.es/api/videos/{asset_id}/subtitulos.json",
        ]
        last_err: Exception | None = None
        for url in urls:
            try:
                data = self._http.get_json(url)
                items = data.get("page", {}).get("items", [])
                if isinstance(items, list) and items:
                    return items
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        return []

