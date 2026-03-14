from __future__ import annotations

import math
import re
import statistics
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from difflib import SequenceMatcher

from rtve_dl.log import debug, error
from rtve_dl.ffmpeg import probe_duration_seconds, run_ffmpeg
from rtve_dl.constants import DEFAULT_SUBTITLE_DELAY_MS
from rtve_dl.rtve.catalog import SeriesAsset
from rtve_dl.subs.srt_parse import parse_srt
from rtve_dl.subs.vtt import Cue
from rtve_dl.asr_mlx import transcribe_es_to_srt_with_mlx_whisper
from rtve_dl.asr_whisperx import transcribe_es_to_srt_with_whisperx


_NORM_RE = re.compile(r"[^a-z0-9а-яёñáéíóúü]+", re.IGNORECASE)
AUTO_DELAY_ASR_SEGMENT_S = 300
ASR_MATCH_SIM_MIN = 0.66
ASR_MIN_MATCHES = 12
ASR_SHORT_TEXT_CHARS = 8
ASR_SHORT_TEXT_STRONG_SIM_MIN = 0.88
ASR_DELAY_CLUSTER_MS = 350
ENERGY_PCTL = 0.55
ENERGY_FLOOR = 400
AUTO_DELAY_PRIMARY_START_S = 60.0


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


def _text_char_len(s: str) -> int:
    return len(s.replace(" ", ""))


def _weighted_median(samples: list[tuple[int, float]]) -> int:
    ordered = sorted(samples, key=lambda item: item[0])
    total = sum(weight for _, weight in ordered)
    if total <= 0:
        return ordered[len(ordered) // 2][0]
    threshold = total / 2.0
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= threshold:
            return value
    return ordered[-1][0]


def _select_delay_cluster(matches: list[tuple[int, float]]) -> tuple[int, int, float] | None:
    if len(matches) < ASR_MIN_MATCHES:
        return None
    total_weight = sum(weight for _, weight in matches)
    if total_weight <= 0:
        return None
    best_center = 0
    best_score = -1.0
    for center, _ in matches:
        score = sum(weight for delta, weight in matches if abs(delta - center) <= ASR_DELAY_CLUSTER_MS)
        if score > best_score:
            best_score = score
            best_center = center
    inliers = [(delta, weight) for delta, weight in matches if abs(delta - best_center) <= ASR_DELAY_CLUSTER_MS]
    if len(inliers) < ASR_MIN_MATCHES:
        return None
    delay_ms = _weighted_median(inliers)
    return delay_ms, len(inliers), max(0.0, min(1.0, best_score / total_weight))


def _base_from_asset(a: SeriesAsset) -> str:
    title = (a.title or a.asset_id or "").strip().lower()
    title = re.sub(r"[^a-z0-9]+", "_", title).strip("_")
    if not title:
        title = "episode"
    season = a.season or 0
    episode = a.episode or 0
    return f"S{season:02d}E{episode:02d}_{title[:80]}"


def _auto_delay_clip_starts(duration_s: float | None) -> list[float | None]:
    if duration_s is None or duration_s <= AUTO_DELAY_ASR_SEGMENT_S:
        return [None]

    max_start = max(0.0, duration_s - AUTO_DELAY_ASR_SEGMENT_S)
    primary = min(AUTO_DELAY_PRIMARY_START_S, max_start)
    middle = max(0.0, (duration_s / 2.0) - (AUTO_DELAY_ASR_SEGMENT_S / 2.0))
    starts: list[float | None] = [primary]
    if abs(middle - primary) >= 1.0:
        starts.append(middle)
    return starts


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
    debug(f"subtitle auto-delay: ffmpeg extract audio {mp4_path} (bin_ms={bin_ms})")
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
    # percentile + floor threshold
    idx = int(ENERGY_PCTL * (len(sorted_vals) - 1))
    thr = max(ENERGY_FLOOR, sorted_vals[idx])
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
    save_asr_srt: Path | None = None,
) -> DelayEstimate | None:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    duration_s = probe_duration_seconds(mp4_path)
    for start_s in _auto_delay_clip_starts(duration_s):
        clip_path: Path | None = None
        clip_source = mp4_path
        clip_start_ms = 0
        clip_end_ms = 0
        if start_s is not None:
            clip_start_ms = int(start_s * 1000)
            clip_end_ms = int((start_s + AUTO_DELAY_ASR_SEGMENT_S) * 1000)
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=f"auto_delay.{base}.",
                suffix=".wav",
                dir=tmp_dir,
                delete=False,
            ) as tmp_clip:
                clip_path = Path(tmp_clip.name)
            try:
                run_ffmpeg(
                    [
                        "-y",
                        "-ss",
                        f"{start_s:.3f}",
                        "-t",
                        f"{AUTO_DELAY_ASR_SEGMENT_S:.3f}",
                        "-i",
                        str(mp4_path),
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-f",
                        "wav",
                        str(clip_path),
                    ]
                )
                debug(
                    "subtitle auto-delay ASR clip: "
                    f"start={start_s:.1f}s dur={AUTO_DELAY_ASR_SEGMENT_S}s"
                )
                clip_source = clip_path
            except Exception:
                try:
                    clip_path.unlink()
                except OSError:
                    pass
                clip_path = None
                continue
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f"auto_delay.{base}.",
            suffix=".srt",
            dir=tmp_dir,
            delete=False,
        ) as tmp:
            asr_srt = Path(tmp.name)
        try:
            if asr_backend == "mlx":
                transcribe_es_to_srt_with_mlx_whisper(
                    media_path=clip_source, out_srt=asr_srt, model_repo=asr_mlx_model
                )
            else:
                transcribe_es_to_srt_with_whisperx(
                    media_path=clip_source,
                    out_srt=asr_srt,
                    model=asr_model,
                    device=asr_device,
                    compute_type="int8",
                    batch_size=asr_batch_size,
                    vad_method=asr_vad_method,
                )
            if save_asr_srt is not None:
                save_asr_srt.parent.mkdir(parents=True, exist_ok=True)
                save_asr_srt.write_text(asr_srt.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                debug(f"subtitle auto-delay ASR transcript saved: {save_asr_srt}")
            asr_cues = parse_srt(asr_srt.read_text(encoding="utf-8", errors="replace"))
        finally:
            try:
                asr_srt.unlink()
            except OSError:
                pass
            if clip_path is not None:
                try:
                    clip_path.unlink()
                except OSError:
                    pass
        if not asr_cues:
            continue
        if clip_start_ms and clip_end_ms:
            sub_t = [
                (_norm_text(c.text), c.start_ms)
                for c in cues
                if _norm_text(c.text) and clip_start_ms <= c.start_ms <= clip_end_ms
            ]
            asr_t = [
                (_norm_text(c.text), c.start_ms + clip_start_ms)
                for c in asr_cues
                if _norm_text(c.text)
            ]
        else:
            sub_t = [(_norm_text(c.text), c.start_ms) for c in cues if _norm_text(c.text)]
            asr_t = [(_norm_text(c.text), c.start_ms) for c in asr_cues if _norm_text(c.text)]
        if not sub_t or not asr_t:
            continue

        matches: list[tuple[int, float]] = []
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
            if best_j >= 0 and best_sim >= ASR_MATCH_SIM_MIN:
                text_chars = min(_text_char_len(st), _text_char_len(asr_t[best_j][0]))
                if text_chars < ASR_SHORT_TEXT_CHARS and best_sim < ASR_SHORT_TEXT_STRONG_SIM_MIN:
                    continue
                # Positive delay means subtitles should move later.
                delta = asr_t[best_j][1] - s_ms
                if abs(delta) <= max_ms:
                    weight = best_sim * max(1.0, min(32.0, float(text_chars)))
                    matches.append((delta, weight))
                    sims.append(best_sim)
                    j = best_j
        selected = _select_delay_cluster(matches)
        if selected is None:
            continue
        delay_ms, matched, cluster_ratio = selected
        debug(
            "subtitle auto-delay ASR cluster: "
            f"delay_ms={delay_ms} matched={matched}/{len(matches)} cluster_ratio={cluster_ratio:.3f}"
        )
        confidence = cluster_ratio * min(1.0, matched / 40.0) * (sum(sims) / len(sims))
        return DelayEstimate(delay_ms=delay_ms, confidence=confidence, method="asr", matched=matched)

    return None


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
    asr_backend: str,
    asr_model: str,
    asr_device: str,
    asr_compute_type: str,
    asr_batch_size: int,
    asr_vad_method: str,
    asr_mlx_model: str,
    save_asr_srt_dir: Path | None = None,
) -> int:
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
    for base, mp4, srt in local_candidates:
        try:
            cues = parse_srt(srt.read_text(encoding="utf-8", errors="replace"))
            est = _estimate_by_energy(cues, mp4, max_ms=max_ms)
            if est is None or est.confidence < 0.10:
                debug(f"subtitle auto-delay: low-confidence energy on {base}, trying ASR")
                asr_est = _estimate_by_asr(
                    cues=cues,
                    mp4_path=mp4,
                    tmp_dir=cache_dir,
                    base=base,
                    asr_backend=asr_backend,
                    asr_model=asr_model,
                    asr_device=asr_device,
                    asr_compute_type=asr_compute_type,
                    asr_batch_size=asr_batch_size,
                    asr_vad_method=asr_vad_method,
                    asr_mlx_model=asr_mlx_model,
                    max_ms=max_ms,
                    save_asr_srt=(
                        save_asr_srt_dir / f"{base}.auto_delay.{asr_backend}.srt"
                        if save_asr_srt_dir is not None else None
                    ),
                )
                if asr_est is not None:
                    est = asr_est
                elif est is not None:
                    debug(
                        "subtitle auto-delay: discarding low-confidence energy estimate "
                        f"for {base} because ASR produced no usable match"
                    )
                    est = None
            if est is None:
                continue
            estimates.append(est)
        except Exception as e:
            error(f"subtitle auto-delay sample failed for {base}: {e}")

    if not estimates:
        error(f"subtitle auto-delay: all samples failed, fallback to {DEFAULT_SUBTITLE_DELAY_MS}ms")
        return DEFAULT_SUBTITLE_DELAY_MS

    if scope == "episode":
        return int(estimates[0].delay_ms)

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
    return med
