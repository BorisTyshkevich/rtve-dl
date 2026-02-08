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


def download_to_mp4(input_url: str, out_mp4: Path, *, headers: dict[str, str] | None = None) -> None:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    debug(f"download_to_mp4: {input_url} -> {out_mp4}")
    args: list[str] = ["-y"]
    if headers:
        # ffmpeg expects CRLF separated headers.
        hdr = "".join([f"{k}: {v}\r\n" for k, v in headers.items()])
        args += ["-headers", hdr]
    # For HLS this will remux; for MP4 it will copy. If it fails, user can pick another URL.
    args += ["-i", input_url, "-c", "copy", str(out_mp4)]
    run_ffmpeg(args)


def mux_mkv(
    *,
    video_path: Path,
    out_mkv: Path,
    subs: list[tuple[Path, str, str]],
) -> None:
    """
    subs: list of (path, language, title). Codec will be SRT-in-MKV.
    """
    out_mkv.parent.mkdir(parents=True, exist_ok=True)
    debug(f"mux_mkv: video={video_path} out={out_mkv} subs={len(subs)}")
    args: list[str] = ["-y", "-i", str(video_path)]
    for p, _lang, _title in subs:
        args += ["-i", str(p)]

    # Map all streams: video+audio from input 0; then each subtitle input.
    args += ["-map", "0"]
    for i in range(1, 1 + len(subs)):
        args += ["-map", str(i)]

    args += ["-c", "copy", "-c:s", "srt"]

    # Attach metadata per subtitle stream.
    for idx, (_p, lang, title) in enumerate(subs):
        args += [f"-metadata:s:s:{idx}", f"language={lang}"]
        args += [f"-metadata:s:s:{idx}", f"title={title}"]

    args += [str(out_mkv)]
    run_ffmpeg(args)
