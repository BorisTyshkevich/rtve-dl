from __future__ import annotations

from dataclasses import dataclass

from rtve_dl.subtitle_tracks.models import (
    TRACK_REFS,
    TRACK_REFS_ASR,
    TRACK_RU,
    TRACK_RU_ASR,
    TRACK_RU_DUAL,
    TRACK_RU_DUAL_ASR,
)

_TRACKS = {"es", "en", "ru", "ru-dual", "refs"}
_MODES = {"off", "on", "require"}
_DEFAULTS = {
    "es": "on",
    "en": "on",
    "ru": "require",
    "ru-dual": "on",
    "refs": "on",
}


@dataclass(frozen=True)
class TrackPolicy:
    modes: dict[str, str]

    def mode(self, track: str) -> str:
        return self.modes[track]

    def enabled(self, track: str) -> bool:
        return self.mode(track) != "off"

    def required(self, track: str) -> bool:
        return self.mode(track) == "require"


def parse_track_policy(raw_entries: list[str] | None) -> TrackPolicy:
    modes = dict(_DEFAULTS)
    for raw in raw_entries or []:
        entry = (raw or "").strip()
        if not entry:
            continue
        if "=" not in entry:
            raise RuntimeError(f"invalid --sub value: {entry!r}. Expected <track>=<off|on|require>.")
        track_raw, mode_raw = entry.split("=", 1)
        track = track_raw.strip().lower()
        mode = mode_raw.strip().lower()
        if track not in _TRACKS:
            allowed = ", ".join(sorted(_TRACKS))
            raise RuntimeError(f"invalid --sub track: {track!r}. Allowed: {allowed}")
        if mode not in _MODES:
            allowed = ", ".join(sorted(_MODES))
            raise RuntimeError(f"invalid --sub mode for {track!r}: {mode!r}. Allowed: {allowed}")
        modes[track] = mode

    # ru-dual depends on ru: promote ru when needed.
    ru_dual_mode = modes["ru-dual"]
    if ru_dual_mode != "off" and modes["ru"] == "off":
        modes["ru"] = "require" if ru_dual_mode == "require" else "on"

    return TrackPolicy(modes=modes)


def enabled_ru_track_ids(*, policy: TrackPolicy, force_asr: bool) -> set[str]:
    out: set[str] = set()
    if policy.enabled("ru"):
        out.add(TRACK_RU_ASR if force_asr else TRACK_RU)
    if policy.enabled("refs"):
        out.add(TRACK_REFS_ASR if force_asr else TRACK_REFS)
    if policy.enabled("ru-dual"):
        out.add(TRACK_RU_DUAL_ASR if force_asr else TRACK_RU_DUAL)
    return out
