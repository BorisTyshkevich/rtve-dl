from __future__ import annotations

from dataclasses import dataclass

from rtve_dl.http import HttpClient
from rtve_dl.rtve.api import RtveApi
from rtve_dl.rtve.png_thumbnail import RtveThumbnailResolver


@dataclass(frozen=True)
class ResolvedAsset:
    asset_id: str
    title: str | None
    video_urls: list[str]
    subtitles_es_vtt: str | None
    subtitles_en_vtt: str | None


class RtveResolver:
    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient()
        self._api = RtveApi(self._http)
        self._thumb = RtveThumbnailResolver(self._http)

    def resolve(self, asset_id: str, *, ignore_drm: bool = False) -> ResolvedAsset:
        meta = self._api.get_video_meta(asset_id)
        if meta.has_drm and not ignore_drm:
            raise RuntimeError(f"DRM protected asset: {asset_id} ({meta.title or ''})")

        subs = self._api.get_subtitles(asset_id)
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

        urls = self._thumb.resolve(asset_id)
        # Prefer HLS master manifest if present.
        urls_sorted = []
        for u in urls:
            if u.endswith(".m3u8") and "video.m3u8" in u:
                urls_sorted.append(u)
        for u in urls:
            if u not in urls_sorted:
                urls_sorted.append(u)

        if not urls_sorted:
            raise RuntimeError(f"could not resolve video urls for asset {asset_id}")

        return ResolvedAsset(
            asset_id=asset_id,
            title=meta.title,
            video_urls=urls_sorted,
            subtitles_es_vtt=es,
            subtitles_en_vtt=en,
        )
