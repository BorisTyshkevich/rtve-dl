from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rtve_dl.log import debug, is_debug


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")


def run_ffmpeg(args: list[str]) -> None:
    require_ffmpeg()
    base = ["ffmpeg", "-hide_banner", "-nostdin"]
    if is_debug():
        # Show progress for long downloads/mux operations.
        base += ["-loglevel", "warning", "-stats"]
        debug("ffmpeg " + " ".join(args))
    else:
        base += ["-loglevel", "error"]
    p = subprocess.run([*base, *args], text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(args)}")


def is_valid_mp4(path: Path) -> bool:
    """
    Best-effort MP4 integrity check for cache-hit decisions.
    """
    if not path.exists() or path.stat().st_size == 0:
        return False

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        p = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if p.returncode != 0:
            return False
        out = (p.stdout or "").strip()
        return bool(out)

    # Fallback if ffprobe is unavailable.
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return p.returncode == 0


def download_to_mp4(input_url: str, out_mp4: Path, *, headers: dict[str, str] | None = None) -> None:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    debug(f"download_to_mp4: {input_url} -> {out_mp4}")
    if out_mp4.exists():
        debug(f"cache hit mp4: {out_mp4}")
        return

    # Always download to a temporary file first, then atomically rename.
    part_mp4 = out_mp4.with_name(out_mp4.name + ".partial.mp4")

    # For direct MP4 URLs prefer curl resume; ffmpeg remux from a byte range is not
    # a safe "append" strategy for already-partial MP4 output files.
    if ".mp4" in input_url and shutil.which("curl") is not None:
        cmd: list[str] = [
            "curl",
            "--location",
            "--fail",
            "--silent",
            "--show-error",
            "--continue-at",
            "-",
            "--output",
            str(part_mp4),
            "--user-agent",
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/144.0.0.0 Safari/537.36"
            ),
        ]
        if headers:
            for k, v in headers.items():
                cmd += ["--header", f"{k}: {v}"]
        cmd += [input_url]
        debug(" ".join(cmd))
        p = subprocess.run(cmd, text=True)
        if p.returncode == 0:
            part_mp4.replace(out_mp4)
            return
        debug(f"curl resume failed (exit {p.returncode}); falling back to ffmpeg: {input_url}")

    args: list[str] = ["-y"]
    if headers:
        # ffmpeg expects CRLF separated headers.
        hdr = "".join([f"{k}: {v}\r\n" for k, v in headers.items()])
        args += ["-headers", hdr]
    # For HLS this will remux; for MP4 it will copy. If it fails, user can pick another URL.
    args += ["-i", input_url, "-c", "copy", str(part_mp4)]
    run_ffmpeg(args)
    part_mp4.replace(out_mp4)


def mux_mkv(
    *,
    video_path: Path,
    out_mkv: Path,
    subs: list[tuple[Path, str, str]],
    subtitle_delay_ms: int = 0,
) -> None:
    """
    subs: list of (path, language, title). Codec will be SRT-in-MKV.
    """
    out_mkv.parent.mkdir(parents=True, exist_ok=True)
    debug(
        "mux_mkv: "
        f"video={video_path} out={out_mkv} subs={len(subs)} subtitle_delay_ms={subtitle_delay_ms}"
    )
    args: list[str] = ["-y", "-i", str(video_path)]
    subtitle_offset_sec = f"{subtitle_delay_ms / 1000.0:.3f}"
    for p, _lang, _title in subs:
        # Apply subtitle delay at mux stage only; keep cached SRT files unchanged.
        args += ["-itsoffset", subtitle_offset_sec, "-i", str(p)]

    # Map all streams: video+audio from input 0; then each subtitle input.
    args += ["-map", "0"]
    for i in range(1, 1 + len(subs)):
        args += ["-map", str(i)]

    # Copy primary A/V streams, re-encode subtitle inputs as SRT-in-MKV.
    args += ["-c:v", "copy", "-c:a", "copy", "-c:s", "srt"]

    # Attach metadata per subtitle stream.
    for idx, (_p, lang, title) in enumerate(subs):
        args += [f"-metadata:s:s:{idx}", f"language={lang}"]
        args += [f"-metadata:s:s:{idx}", f"title={title}"]

    args += [str(out_mkv)]
    run_ffmpeg(args)
