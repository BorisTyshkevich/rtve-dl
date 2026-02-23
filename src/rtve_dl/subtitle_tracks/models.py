from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TRACK_ES = "es"
TRACK_ES_ASR = "es_asr"
TRACK_EN = "en"
TRACK_EN_ASR = "en_asr"
TRACK_RU = "ru"
TRACK_RU_ASR = "ru_asr"
TRACK_RU_DUAL = "ru_dual"
TRACK_RU_DUAL_ASR = "ru_dual_asr"
TRACK_REFS = "refs"
TRACK_REFS_ASR = "refs_asr"


@dataclass(frozen=True)
class ProducedTrack:
    id: str
    path: Path
    lang: str
    title: str
