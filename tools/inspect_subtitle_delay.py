from __future__ import annotations

import argparse
from pathlib import Path

from rtve_dl.log import set_debug
from rtve_dl.subs.delay_auto import _estimate_by_asr, _estimate_by_energy
from rtve_dl.subs.srt_parse import parse_srt
from rtve_dl.subs.vtt import parse_vtt


def _format_estimate(label: str, est: object | None) -> str:
    if est is None:
        return f"{label}: none"
    return (
        f"{label}: delay_ms={est.delay_ms} confidence={est.confidence:.3f} "
        f"method={est.method} matched={est.matched}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect subtitle auto-delay estimation for one local MP4 + SRT pair."
    )
    parser.add_argument("--mp4", required=True, help="Path to source MP4")
    parser.add_argument("--srt", default=None, help="Path to Spanish SRT")
    parser.add_argument("--vtt", default=None, help="Path to Spanish RTVE VTT; use this for parity with production")
    parser.add_argument(
        "--tmp-dir",
        default="tmp/subtitle-delay-inspect",
        help="Directory for temporary ASR clip/output files",
    )
    parser.add_argument("--base", default=None, help="Base name used in temp file names")
    parser.add_argument("--max-ms", type=int, default=15000, help="Maximum absolute delay to consider")
    parser.add_argument("--skip-energy", action="store_true", help="Skip energy-based estimation")
    parser.add_argument("--skip-asr", action="store_true", help="Skip ASR-based estimation")
    parser.add_argument("--save-asr-srt", default=None, help="Optional path to save the ASR SRT used for delay estimation")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--asr-backend",
        default="whisperx",
        choices=["whisperx", "mlx"],
        help="Python auto-delay ASR backend",
    )
    parser.add_argument("--asr-model", default="small", help="WhisperX model name")
    parser.add_argument("--asr-device", default="cpu", help="WhisperX device")
    parser.add_argument("--asr-compute-type", default="int8", help="WhisperX compute type")
    parser.add_argument("--asr-batch-size", type=int, default=8, help="WhisperX batch size")
    parser.add_argument("--asr-vad-method", default="silero", help="WhisperX VAD method")
    parser.add_argument("--asr-mlx-model", default="mlx-community/whisper-small-mlx", help="MLX model repo")
    args = parser.parse_args()

    set_debug(args.debug)
    mp4 = Path(args.mp4)
    srt = Path(args.srt) if args.srt else None
    vtt = Path(args.vtt) if args.vtt else None
    tmp_dir = Path(args.tmp_dir)
    base = args.base or mp4.stem

    if not mp4.exists():
        raise SystemExit(f"missing mp4 file: {mp4}")
    if vtt is None and srt is None:
        raise SystemExit("one of --vtt or --srt is required")
    if vtt is not None:
        if not vtt.exists():
            raise SystemExit(f"missing vtt file: {vtt}")
        cues = parse_vtt(vtt.read_text(encoding="utf-8", errors="replace"))
        source_label = f"vtt: {vtt}"
    else:
        if srt is None or not srt.exists():
            raise SystemExit(f"missing srt file: {srt}")
        cues = parse_srt(srt.read_text(encoding="utf-8", errors="replace"))
        source_label = f"srt: {srt}"
    if not cues:
        raise SystemExit(f"no cues found in {source_label}")

    energy_est = None
    asr_est = None
    if not args.skip_energy:
        energy_est = _estimate_by_energy(cues, mp4, max_ms=max(1, args.max_ms))
    if not args.skip_asr:
        asr_est = _estimate_by_asr(
            cues=cues,
            mp4_path=mp4,
            tmp_dir=tmp_dir,
            base=base,
            asr_backend=args.asr_backend,
            asr_model=args.asr_model,
            asr_device=args.asr_device,
            asr_compute_type=args.asr_compute_type,
            asr_batch_size=args.asr_batch_size,
            asr_vad_method=args.asr_vad_method,
            asr_mlx_model=args.asr_mlx_model,
            max_ms=max(1, args.max_ms),
            save_asr_srt=Path(args.save_asr_srt) if args.save_asr_srt else None,
        )

    if energy_est is None and asr_est is None:
        final_est = None
    elif energy_est is None:
        final_est = asr_est
    elif energy_est.confidence >= 0.10 or asr_est is None:
        final_est = energy_est
    else:
        final_est = asr_est

    print(f"mp4: {mp4}")
    print(source_label)
    print(f"cues: {len(cues)}")
    print(_format_estimate("energy", energy_est))
    print(_format_estimate("asr", asr_est))
    print(_format_estimate("final", final_est))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
