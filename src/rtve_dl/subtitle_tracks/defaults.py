from __future__ import annotations

from rtve_dl.subtitle_tracks.models import (
    TRACK_EN,
    TRACK_EN_ASR,
    TRACK_ES,
    TRACK_ES_ASR,
    TRACK_REFS,
    TRACK_REFS_ASR,
    TRACK_RU,
    TRACK_RU_ASR,
    TRACK_RU_DUAL,
    TRACK_RU_DUAL_ASR,
    ProducedTrack,
)

_DEFAULT_SELECTIONS: dict[str, tuple[str, ...]] = {
    "es": (TRACK_ES_ASR, TRACK_ES),
    "en": (TRACK_EN_ASR, TRACK_EN),
    "ru": (TRACK_RU_ASR, TRACK_RU),
    "ru-dual": (TRACK_RU_DUAL_ASR, TRACK_RU_DUAL),
    "refs": (TRACK_REFS_ASR, TRACK_REFS),
}


def resolve_default_subtitle_title(subs: list[ProducedTrack], requested: str) -> str:
    key = (requested or "").strip().lower()
    if key not in _DEFAULT_SELECTIONS:
        allowed = ", ".join(sorted(_DEFAULT_SELECTIONS))
        raise RuntimeError(f"invalid default subtitle: {requested}. Allowed: {allowed}")
    wanted = set(_DEFAULT_SELECTIONS[key])
    for sub in subs:
        if sub.id in wanted:
            return sub.title
    raise RuntimeError(f"default subtitle '{key}' is not available in produced tracks")
