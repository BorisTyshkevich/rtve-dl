from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from rtve_dl.log import set_debug
from rtve_dl.subs.delay_auto import _estimate_by_asr, _estimate_by_energy
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.srt_parse import parse_srt
from rtve_dl.subs.sync_compare import parse_rust_inspector_output, resolve_ilass_binary, run_ilass, summarize_shift_deltas
from rtve_dl.subs.vtt import parse_vtt


def _format_estimate(label: str, est: object | None) -> str:
    if est is None:
        return f"{label}: none"
    return (
        f"{label}: delay_ms={est.delay_ms} confidence={est.confidence:.3f} "
        f"method={est.method} matched={est.matched}"
    )


def _run_rust_inspector(
    *,
    repo_root: Path,
    mp4: Path,
    source_path: Path,
    source_kind: str,
    max_ms: int,
    skip_energy: bool,
    debug: bool,
) -> str:
    cmd = [
        "cargo",
        "run",
        "--manifest-path",
        str(repo_root / "rust" / "Cargo.toml"),
        "--bin",
        "inspect_subtitle_delay",
        "--",
        "--mp4",
        str(mp4),
        f"--{source_kind}",
        str(source_path),
        "--max-ms",
        str(max_ms),
    ]
    if skip_energy:
        cmd.append("--skip-energy")
    if debug:
        cmd.append("--debug")
    p = subprocess.run(cmd, cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"rust inspector failed:\n{p.stdout}")
    return p.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Python/Rust subtitle delay estimates against ilass on the same local media."
    )
    parser.add_argument("--mp4", required=True, help="Path to source MP4")
    parser.add_argument("--srt", default=None, help="Path to Spanish SRT")
    parser.add_argument("--vtt", default=None, help="Path to Spanish RTVE VTT; use this for production parity")
    parser.add_argument("--out-dir", default="tmp/debug/sync-compare", help="Directory for generated artifacts")
    parser.add_argument("--ilass-bin", default=None, help="Override ilass binary path")
    parser.add_argument("--tmp-dir", default="tmp/subtitle-delay-inspect", help="Directory for temporary ASR files")
    parser.add_argument("--base", default=None, help="Base name used in temp file names")
    parser.add_argument("--max-ms", type=int, default=15000, help="Maximum absolute delay to consider")
    parser.add_argument("--skip-energy", action="store_true", help="Skip Python/Rust energy estimation")
    parser.add_argument("--skip-asr", action="store_true", help="Skip Python ASR estimation")
    parser.add_argument("--run-rust", action="store_true", help="Also run the Rust inspector for direct comparison")
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
    repo_root = Path(__file__).resolve().parents[1]
    mp4 = Path(args.mp4)
    srt = Path(args.srt) if args.srt else None
    vtt = Path(args.vtt) if args.vtt else None
    out_dir = Path(args.out_dir)
    tmp_dir = Path(args.tmp_dir)
    base = args.base or mp4.stem

    if not mp4.exists():
        raise SystemExit(f"missing mp4 file: {mp4}")
    if vtt is None and srt is None:
        raise SystemExit("one of --vtt or --srt is required")

    source_kind = "vtt" if vtt is not None else "srt"
    source_path = vtt if vtt is not None else srt
    assert source_path is not None
    if not source_path.exists():
        raise SystemExit(f"missing {source_kind} file: {source_path}")

    if source_kind == "vtt":
        cues = parse_vtt(source_path.read_text(encoding="utf-8", errors="replace"))
    else:
        cues = parse_srt(source_path.read_text(encoding="utf-8", errors="replace"))
    if not cues:
        raise SystemExit(f"no cues found in {source_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    baseline_srt = out_dir / f"{base}.baseline.srt"
    baseline_srt.write_text(cues_to_srt(cues), encoding="utf-8")
    ilass_out = out_dir / f"{base}.ilass.srt"

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
        )
    if energy_est is None and asr_est is None:
        final_est = None
    elif energy_est is None:
        final_est = asr_est
    elif energy_est.confidence >= 0.10 or asr_est is None:
        final_est = energy_est
    else:
        final_est = asr_est

    ilass_bin = resolve_ilass_binary(args.ilass_bin)
    run_ilass(ilass_bin=ilass_bin, reference_path=mp4, input_srt=baseline_srt, output_srt=ilass_out)
    ilass_cues = parse_srt(ilass_out.read_text(encoding="utf-8", errors="replace"))
    ilass_summary = summarize_shift_deltas(cues, ilass_cues)

    rust_summary = None
    if args.run_rust:
        rust_stdout = _run_rust_inspector(
            repo_root=repo_root,
            mp4=mp4,
            source_path=source_path,
            source_kind=source_kind,
            max_ms=max(1, args.max_ms),
            skip_energy=args.skip_energy,
            debug=args.debug,
        )
        rust_summary = parse_rust_inspector_output(rust_stdout)

    print(f"mp4: {mp4}")
    print(f"{source_kind}: {source_path}")
    print(f"baseline_srt: {baseline_srt}")
    print(_format_estimate("python_energy", energy_est))
    print(_format_estimate("python_asr", asr_est))
    print(_format_estimate("python_final", final_est))
    if rust_summary is None:
        print("rust_final: skipped")
    else:
        print(f"rust_final: delay_ms={rust_summary.delay_ms} confidence={rust_summary.confidence:.3f}")
    print(
        "ilass_shift: "
        f"median_ms={ilass_summary.median_ms} min_ms={ilass_summary.min_ms} "
        f"max_ms={ilass_summary.max_ms} unique_count={ilass_summary.unique_count} "
        f"sample={list(ilass_summary.unique_sample_ms)}"
    )
    print(f"ilass_out: {ilass_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
