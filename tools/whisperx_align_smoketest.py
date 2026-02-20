from __future__ import annotations

import argparse
from pathlib import Path

from rtve_dl.subs.align_whisperx import align_cues_with_whisperx
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.srt_parse import parse_srt


def main() -> int:
    parser = argparse.ArgumentParser(description="WhisperX alignment smoke test.")
    parser.add_argument("--audio", required=True, help="Path to WAV audio fixture")
    parser.add_argument("--srt", required=True, help="Path to SRT subtitle snippet")
    parser.add_argument("--out", required=True, help="Path to output aligned SRT")
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "cpu"])
    parser.add_argument("--align-model", default=None, help="Override alignment model")
    args = parser.parse_args()

    audio = Path(args.audio)
    srt = Path(args.srt)
    out = Path(args.out)

    if not audio.exists():
        raise SystemExit(f"missing audio file: {audio}")
    if not srt.exists():
        raise SystemExit(f"missing srt file: {srt}")

    cues = parse_srt(srt.read_text(encoding="utf-8", errors="replace"))
    if not cues:
        raise SystemExit("no cues found in VTT fixture")

    aligned = align_cues_with_whisperx(
        media_path=audio,
        cues=cues,
        device_mode=args.device,
        align_model=args.align_model,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(cues_to_srt(aligned), encoding="utf-8")
    print(f"aligned cues: {len(aligned)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
