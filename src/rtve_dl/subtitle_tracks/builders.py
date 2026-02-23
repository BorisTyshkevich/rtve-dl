from __future__ import annotations

import re
from pathlib import Path

from rtve_dl.log import debug
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.srt_parse import parse_srt
from rtve_dl.subs.vtt import Cue


def _is_nonempty_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _remove_if_empty(path: Path) -> None:
    if not path.exists():
        return
    try:
        if path.stat().st_size > 0:
            return
    except OSError:
        return
    path.unlink(missing_ok=True)


def _normalize_ru_refs_candidate(raw: str | None) -> str:
    t = (raw or "").strip()
    if not t:
        return ""
    if "\t" in t:
        t = " ".join(x.strip() for x in t.split("\t") if x.strip())
    return re.sub(r"\s+", " ", t).strip()


def _spanish_tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-záéíóúñü]+", (s or "").lower()))


def _looks_like_inline_annotated_spanish(es_text: str, candidate: str) -> bool:
    es = (es_text or "").strip()
    out = (candidate or "").strip()
    if not out:
        return False
    if ";" in out and "(" not in out and ")" not in out:
        return False
    es_tokens = _spanish_tokens(es)
    out_tokens = _spanish_tokens(out)
    overlap = len(es_tokens & out_tokens)
    min_overlap = 1 if len(es_tokens) <= 3 else 2
    if overlap < min_overlap:
        return False
    if ("(" in out or ")" in out) and not re.search(r"\([^\)]*[А-Яа-яЁё][^\)]*\)", out):
        return False
    return True


def compose_ref_text(es_text: str, ru_refs: str) -> str:
    es = (es_text or "").strip()
    candidate = _normalize_ru_refs_candidate(ru_refs)
    if not candidate:
        return es
    if _looks_like_inline_annotated_spanish(es, candidate):
        return candidate
    return es


def build_ru_srt(*, srt_path: Path, cues: list, ru_map: dict[str, str]) -> None:
    _remove_if_empty(srt_path)
    if _is_nonempty_file(srt_path):
        debug(f"cache hit srt: {srt_path}")
        return
    ru_cues = [
        Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=ru_map.get(f"{i}", ""))
        for i, c in enumerate(cues)
    ]
    srt_path.write_text(cues_to_srt(ru_cues), encoding="utf-8")


def build_refs_srt(*, srt_path: Path, cues: list, refs_map: dict[str, str]) -> None:
    _remove_if_empty(srt_path)
    if _is_nonempty_file(srt_path):
        debug(f"cache hit srt: {srt_path}")
        return
    ref_cues = [
        Cue(
            start_ms=c.start_ms,
            end_ms=c.end_ms,
            text=compose_ref_text((c.text or "").strip(), refs_map.get(f"{i}", "")),
        )
        for i, c in enumerate(cues)
    ]
    srt_path.write_text(cues_to_srt(ref_cues), encoding="utf-8")


def build_ru_dual_srt(*, srt_path: Path, cues: list, ru_map: dict[str, str], ru_srt_fallback: Path) -> None:
    _remove_if_empty(srt_path)
    if _is_nonempty_file(srt_path):
        debug(f"cache hit srt: {srt_path}")
        return
    map_local = ru_map
    if not map_local:
        ru_cues_cached = parse_srt(ru_srt_fallback.read_text(encoding="utf-8"))
        map_local = {f"{i}": (c.text or "").strip() for i, c in enumerate(ru_cues_cached)}
    dual_cues = [
        Cue(
            start_ms=c.start_ms,
            end_ms=c.end_ms,
            text=((c.text or "").strip() + "\n" + (map_local.get(f"{i}", "") or "").strip()).strip(),
        )
        for i, c in enumerate(cues)
    ]
    srt_path.write_text(cues_to_srt(dual_cues), encoding="utf-8")
