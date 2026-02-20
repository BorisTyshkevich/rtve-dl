from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.vtt import Cue, parse_vtt


def _shift_cues(cues: list[Cue], offset_ms: int) -> list[Cue]:
    out: list[Cue] = []
    for c in cues:
        start = max(0, c.start_ms - offset_ms)
        end = max(start + 1, c.end_ms - offset_ms)
        out.append(Cue(start_ms=start, end_ms=end, text=c.text))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract audio+VTT snippet for alignment fixture.")
    parser.add_argument("--mp4", required=True, help="Source MP4 path")
    parser.add_argument("--vtt", required=True, help="Source VTT path")
    parser.add_argument("--start", required=True, help="Start timestamp (e.g. 00:05:00)")
    parser.add_argument("--duration", required=True, help="Duration (e.g. 00:03:00)")
    parser.add_argument("--out-dir", required=True, help="Output directory for fixture")
    args = parser.parse_args()

    mp4 = Path(args.mp4)
    vtt = Path(args.vtt)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    wav_out = out_dir / "sample_audio.wav"
    srt_out = out_dir / "sample_es.srt"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(mp4),
        "-ss",
        args.start,
        "-t",
        args.duration,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(wav_out),
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {p.stdout}")

    cues = parse_vtt(vtt.read_text(encoding="utf-8", errors="replace"))
    if not cues:
        raise SystemExit("no cues found in VTT")

    # Filter cues that overlap the selected time range.
    # Convert start time to ms.
    h, m, s = args.start.split(":")
    start_ms = (int(h) * 3600 + int(m) * 60 + float(s)) * 1000
    dur_parts = args.duration.split(":")
    if len(dur_parts) == 3:
        dh, dm, ds = dur_parts
        dur_ms = (int(dh) * 3600 + int(dm) * 60 + float(ds)) * 1000
    else:
        dur_ms = float(args.duration) * 1000
    end_ms = start_ms + dur_ms

    sel = [c for c in cues if c.end_ms > start_ms and c.start_ms < end_ms]
    if not sel:
        raise SystemExit("no cues in selected time range")

    shifted = _shift_cues(sel, int(start_ms))
    srt_out.write_text(cues_to_srt(shifted), encoding="utf-8")

    print(f"wrote {wav_out}")
    print(f"wrote {srt_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
