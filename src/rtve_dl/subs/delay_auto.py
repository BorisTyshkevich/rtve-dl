from __future__ import annotations

import json
import math
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from difflib import SequenceMatcher

from rtve_dl.log import debug, error
from rtve_dl.constants import DEFAULT_SUBTITLE_DELAY_MS
from rtve_dl.rtve.catalog import SeriesAsset
from rtve_dl.subs.srt_parse import parse_srt
from rtve_dl.subs.vtt import Cue
from rtve_dl.asr_mlx import transcribe_es_to_srt_with_mlx_whisper
from rtve_dl.asr_whisperx import transcribe_es_to_srt_with_whisperx


_NORM_RE = re.compile(r"[^a-z0-9а-яёñáéíóúü]+", re.IGNORECASE)


@dataclass(frozen=True)
class DelayEstimate:
    delay_ms: int
    confidence: float
    method: str
    matched: int


def _norm_text(s: str) -> str:
    s = (s or "").lower().replace("\n", " ")
    s = _NORM_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _base_from_asset(a: SeriesAsset) -> str:
    title = (a.title or a.asset_id or "").strip().lower()
    title = re.sub(r"[^a-z0-9]+", "_", title).strip("_")
    if not title:
        title = "episode"
    season = a.season or 0
    episode = a.episode or 0
    return f"S{season:02d}E{episode:02d}_{title[:80]}"


def _activity_intervals_from_cues(cues: list[Cue], *, bin_ms: int, n_bins: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for c in cues:
        s = max(0, min(n_bins, c.start_ms // bin_ms))
        e = max(0, min(n_bins, math.ceil(c.end_ms / bin_ms)))
        if e > s:
            out.append((s, e))
    out.sort()
    return out


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ps, pe = merged[-1]
        if s <= pe:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def _overlap_len(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> int:
    i = 0
    j = 0
    total = 0
    while i < len(a) and j < len(b):
        s1, e1 = a[i]
        s2, e2 = b[j]
        s = max(s1, s2)
        e = min(e1, e2)
        if e > s:
            total += e - s
        if e1 <= e2:
            i += 1
        else:
            j += 1
    return total


def _shift_intervals(intervals: list[tuple[int, int]], shift_bins: int, n_bins: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in intervals:
        ss = s + shift_bins
        ee = e + shift_bins
        if ee <= 0 or ss >= n_bins:
            continue
        out.append((max(0, ss), min(n_bins, ee)))
    return out


def _audio_activity_intervals(mp4_path: Path, *, bin_ms: int) -> list[tuple[int, int]]:
    rate = 1000 // bin_ms
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(mp4_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(rate),
        "-f",
        "s16le",
        "-",
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extract failed for delay auto: {mp4_path}")
    raw = p.stdout
    if len(raw) < 2:
        return []
    # 16-bit signed little-endian mono
    vals = [abs(int.from_bytes(raw[i : i + 2], "little", signed=True)) for i in range(0, len(raw) - 1, 2)]
    if not vals:
        return []
    sorted_vals = sorted(vals)
    # 65 percentile + floor threshold
    idx = int(0.65 * (len(sorted_vals) - 1))
    thr = max(350, sorted_vals[idx])
    act = [1 if v >= thr else 0 for v in vals]
    intervals: list[tuple[int, int]] = []
    i = 0
    n = len(act)
    while i < n:
        if act[i] == 0:
            i += 1
            continue
        j = i + 1
        while j < n and act[j] == 1:
            j += 1
        intervals.append((i, j))
        i = j
    return _merge_intervals(intervals)


def _estimate_by_energy(cues: list[Cue], mp4_path: Path, *, max_ms: int) -> DelayEstimate | None:
    bin_ms = 100
    speech = _audio_activity_intervals(mp4_path, bin_ms=bin_ms)
    if not speech:
        return None
    n_bins = speech[-1][1]
    subs = _merge_intervals(_activity_intervals_from_cues(cues, bin_ms=bin_ms, n_bins=n_bins))
    if not subs:
        return None
    max_bins = max_ms // bin_ms
    scores: list[tuple[int, int]] = []
    for lag in range(-max_bins, max_bins + 1):
        # Positive delay means subtitles should move later.
        shifted = _shift_intervals(subs, lag, n_bins)
        sc = _overlap_len(shifted, speech)
        scores.append((lag, sc))
    scores.sort(key=lambda x: x[1], reverse=True)
    best_lag, best_score = scores[0]
    second_score = scores[1][1] if len(scores) > 1 else 0
    total_sub = sum(e - s for s, e in subs)
    if total_sub <= 0:
        return None
    confidence = max(0.0, min(1.0, (best_score - second_score) / max(1, total_sub)))
    return DelayEstimate(
        delay_ms=int(best_lag * bin_ms),
        confidence=confidence,
        method="energy",
        matched=len(subs),
    )


def _estimate_by_asr(
    *,
    cues: list[Cue],
    mp4_path: Path,
    tmp_dir: Path,
    base: str,
    asr_backend: str,
    asr_model: str,
    asr_device: str,
    asr_compute_type: str,
    asr_batch_size: int,
    asr_vad_method: str,
    asr_mlx_model: str,
    max_ms: int,
) -> DelayEstimate | None:
    asr_srt = tmp_dir / f"{base}.auto_delay.asr.srt"
    if not asr_srt.exists() or asr_srt.stat().st_size == 0:
        if asr_backend == "mlx":
            transcribe_es_to_srt_with_mlx_whisper(media_path=mp4_path, out_srt=asr_srt, model_repo=asr_mlx_model)
        else:
            transcribe_es_to_srt_with_whisperx(
                media_path=mp4_path,
                out_srt=asr_srt,
                model=asr_model,
                device=asr_device,
                compute_type=asr_compute_type,
                batch_size=asr_batch_size,
                vad_method=asr_vad_method,
            )
    asr_cues = parse_srt(asr_srt.read_text(encoding="utf-8", errors="replace"))
    if not asr_cues:
        return None
    sub_t = [(_norm_text(c.text), c.start_ms) for c in cues if _norm_text(c.text)]
    asr_t = [(_norm_text(c.text), c.start_ms) for c in asr_cues if _norm_text(c.text)]
    if not sub_t or not asr_t:
        return None

    deltas: list[int] = []
    sims: list[float] = []
    j = 0
    for st, s_ms in sub_t:
        best_sim = 0.0
        best_j = -1
        hi = min(len(asr_t), j + 25)
        for k in range(j, hi):
            at, a_ms = asr_t[k]
            sim = SequenceMatcher(None, st, at).ratio()
            if sim > best_sim:
                best_sim = sim
                best_j = k
        if best_j >= 0 and best_sim >= 0.58:
            delta = s_ms - asr_t[best_j][1]
            if abs(delta) <= max_ms:
                deltas.append(delta)
                sims.append(best_sim)
                j = best_j
    if len(deltas) < 8:
        return None
    delay_ms = int(statistics.median(deltas))
    confidence = min(1.0, len(deltas) / 40.0) * (sum(sims) / len(sims))
    return DelayEstimate(delay_ms=delay_ms, confidence=confidence, method="asr", matched=len(deltas))


def _load_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _save_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def estimate_series_delay_ms(
    *,
    assets: list[SeriesAsset],
    mp4_dir: Path,
    srt_dir: Path,
    cache_dir: Path,
    out_dir: Path,
    scope: str,
    samples: int,
    max_ms: int,
    refresh: bool,
    asr_backend: str,
    asr_model: str,
    asr_device: str,
    asr_compute_type: str,
    asr_batch_size: int,
    asr_vad_method: str,
    asr_mlx_model: str,
) -> int:
    cache_path = cache_dir / "subtitle_delay.auto.json"
    if not refresh:
        cached = _load_cache(cache_path)
        if cached and isinstance(cached.get("delay_ms"), int):
            debug(f"subtitle auto-delay cache hit: {cache_path}")
            return int(cached["delay_ms"])

    local_candidates: list[tuple[str, Path, Path]] = []
    for a in assets:
        base = _base_from_asset(a)
        mp4 = mp4_dir / f"{base}.mp4"
        srt = srt_dir / f"{base}.spa.srt"
        mkv = out_dir / f"{base}.mkv"
        if mp4.exists() and srt.exists() and mp4.stat().st_size > 0 and srt.stat().st_size > 0:
            local_candidates.append((base, mp4, srt))
        elif scope == "episode" and mkv.exists():
            # If episode scope and target is already muxed, no local source to estimate from.
            pass

    if scope == "episode" and local_candidates:
        local_candidates = local_candidates[:1]
    else:
        local_candidates = local_candidates[: max(1, samples)]

    if not local_candidates:
        error(f"subtitle auto-delay: no local mp4+spa.srt samples, fallback to {DEFAULT_SUBTITLE_DELAY_MS}ms")
        return DEFAULT_SUBTITLE_DELAY_MS

    estimates: list[DelayEstimate] = []
    by_ep: dict[str, dict] = {}
    for base, mp4, srt in local_candidates:
        try:
            cues = parse_srt(srt.read_text(encoding="utf-8", errors="replace"))
            est = _estimate_by_energy(cues, mp4, max_ms=max_ms)
            if est is None or est.confidence < 0.10:
                debug(f"subtitle auto-delay: low-confidence energy on {base}, trying ASR")
                asr_est = _estimate_by_asr(
                    cues=cues,
                    mp4_path=mp4,
                    tmp_dir=srt_dir,
                    base=base,
                    asr_backend=asr_backend,
                    asr_model=asr_model,
                    asr_device=asr_device,
                    asr_compute_type=asr_compute_type,
                    asr_batch_size=asr_batch_size,
                    asr_vad_method=asr_vad_method,
                    asr_mlx_model=asr_mlx_model,
                    max_ms=max_ms,
                )
                if asr_est is not None:
                    est = asr_est
            if est is None:
                continue
            estimates.append(est)
            by_ep[base] = {
                "delay_ms": est.delay_ms,
                "confidence": round(est.confidence, 4),
                "method": est.method,
                "matched": est.matched,
            }
        except Exception as e:
            error(f"subtitle auto-delay sample failed for {base}: {e}")

    if not estimates:
        error(f"subtitle auto-delay: all samples failed, fallback to {DEFAULT_SUBTITLE_DELAY_MS}ms")
        return DEFAULT_SUBTITLE_DELAY_MS

    vals = [e.delay_ms for e in estimates]
    med = int(statistics.median(vals))
    abs_dev = [abs(v - med) for v in vals]
    mad = statistics.median(abs_dev) if abs_dev else 0
    if mad > 0:
        inliers = [v for v in vals if abs(v - med) <= max(800, int(2.5 * mad))]
        if inliers:
            med = int(statistics.median(inliers))
    med = max(-max_ms, min(max_ms, med))
    conf = sum(e.confidence for e in estimates) / max(1, len(estimates))
    debug(
        f"subtitle auto-delay computed: delay_ms={med} confidence={conf:.3f} "
        f"samples={len(estimates)}"
    )

    _save_cache(
        cache_path,
        {
            "version": 1,
            "computed_at": int(time.time()),
            "mode": "hybrid",
            "delay_ms": med,
            "confidence": round(conf, 4),
            "sampled_episodes": [x[0] for x in local_candidates],
            "episode_estimates": by_ep,
            "limits": {"max_ms": max_ms, "samples": samples},
        },
    )
    return med
