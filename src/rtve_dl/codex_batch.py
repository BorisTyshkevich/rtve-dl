from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from rtve_dl.log import debug, stage


@dataclass(frozen=True)
class CodexChunkPaths:
    in_jsonl: Path
    out_jsonl: Path


_JSON_LINE_RE = re.compile(r"^\s*\{.*\}\s*$")


def _ensure_codex_on_path() -> None:
    if subprocess.run(["codex", "--version"], capture_output=True, text=True).returncode != 0:
        raise RuntimeError("codex CLI not found on PATH")


def _build_prompt(*, target_language: str, jsonl_payload: str, prompt_mode: str) -> str:
    # Keep it short, strict, and machine-parseable.
    if prompt_mode == "ru_refs_b2plus":
        return (
            "You are annotating Spanish subtitles for language learning.\n"
            "Return JSONL only: exactly one JSON object per input line, matching the input line count.\n"
            "Rules:\n"
            "- Keep the same id.\n"
            "- Output keys must be exactly: id, text\n"
            "- text must be the ORIGINAL Spanish cue with inline Russian glosses in parentheses.\n"
            "- Add glosses only for difficult B2/C1/C2 words or phrases.\n"
            "- Do NOT add glosses for A1/A2/B1 obvious words.\n"
            "- Prefer phrase-level glosses for idioms/set expressions.\n"
            "- If no difficult term exists, return the original Spanish cue unchanged.\n"
            "- Keep punctuation and line breaks stable.\n"
            "- No extra commentary, no markdown.\n"
            "- Do NOT add blank lines.\n"
            "\n"
            "INPUT JSONL:\n"
            f"{jsonl_payload}\n"
        )

    return (
        f"You are translating Spanish subtitles to natural {target_language}.\n"
        "Return JSONL only: exactly one JSON object per input line, matching the input line count.\n"
        "Rules:\n"
        "- Keep the same id.\n"
        "- Output keys must be exactly: id, text\n"
        "- text must be the translation.\n"
        "- No extra commentary, no markdown.\n"
        "- Do NOT repeat the input in the output.\n"
        "- Do NOT add blank lines.\n"
        "- Preserve \\n if present in input text.\n"
        "\n"
        "INPUT JSONL:\n"
        f"{jsonl_payload}\n"
    )


def chunk_cues(
    cues: list[tuple[str, str]],
    *,
    chunk_cues: int,
    base_path: Path,
    io_tag: str,
) -> list[CodexChunkPaths]:
    """
    cues: list of (id, spanish_text)
    """
    if chunk_cues <= 0:
        raise ValueError("chunk_cues must be positive")
    if not io_tag or not io_tag.isascii():
        raise ValueError("io_tag must be a non-empty ASCII string")

    # Include chunk size in filenames so changing --codex-chunk-cues doesn't accidentally
    # reuse stale chunk inputs/outputs from a previous run.
    stem = str(base_path) + f".c{chunk_cues}"

    out: list[CodexChunkPaths] = []
    for i in range(0, len(cues), chunk_cues):
        part = cues[i : i + chunk_cues]
        idx = (i // chunk_cues) + 1
        in_path = Path(stem + f".{io_tag}.in.{idx:04d}.jsonl")
        out_path = Path(stem + f".{io_tag}.out.{idx:04d}.jsonl")
        out.append(CodexChunkPaths(in_jsonl=in_path, out_jsonl=out_path))

        in_path.parent.mkdir(parents=True, exist_ok=True)
        with in_path.open("w", encoding="utf-8") as f:
            for _id, text in part:
                f.write(json.dumps({"id": _id, "text": text}, ensure_ascii=False) + "\n")
    return out


def _parse_jsonl_map(path: Path) -> dict[str, str]:
    """
    Parse {"id": "...", "text": "..."} JSONL into a map.
    Ignores invalid lines.
    """
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or not _JSON_LINE_RE.match(line):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        _id = str(obj.get("id", "")).strip()
        text = obj.get("text")
        if not _id or not isinstance(text, str):
            continue
        out[_id] = text
    return out


def run_codex_chunk(
    *, chunk: CodexChunkPaths, model: str | None, target_language: str, prompt_mode: str
) -> None:
    _ensure_codex_on_path()
    payload = chunk.in_jsonl.read_text(encoding="utf-8")
    prompt = _build_prompt(
        target_language=target_language, jsonl_payload=payload, prompt_mode=prompt_mode
    )

    cmd = ["codex", "exec", "-s", "read-only", "--output-last-message", str(chunk.out_jsonl)]
    if model:
        cmd += ["-m", model]
    cmd.append("-")  # read prompt from stdin

    debug("codex exec: " + " ".join(cmd))
    with stage(f"codex:{target_language.lower()}:chunk:{chunk.out_jsonl.name}"):
        # codex prints a lot of metadata to stdout; keep the downloader output readable.
        # On failure, write the combined stdout/stderr to a log file next to the output JSONL.
        res = subprocess.run(cmd, input=prompt, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if res.returncode != 0:
            log_path = Path(str(chunk.out_jsonl) + ".log")
            log_path.write_text(res.stdout or "", encoding="utf-8", errors="replace")
            # Avoid leaving a 0-byte "cache hit" file behind.
            try:
                if chunk.out_jsonl.exists() and chunk.out_jsonl.stat().st_size == 0:
                    chunk.out_jsonl.unlink()
            except OSError:
                pass

            out = (res.stdout or "").lower()
            if "401 unauthorized" in out or "provided authentication token is expired" in out or "refresh_token_reused" in out:
                raise RuntimeError(
                    "codex exec failed due to expired/invalid auth. "
                    "Run `codex logout` then `codex login --device-auth` (or `printenv OPENAI_API_KEY | codex login --with-api-key`) "
                    f"and retry. Details: {log_path}"
                )
            if "429" in out or "rate limit" in out or "too many requests" in out:
                raise RuntimeError(f"codex rate limited; see {log_path}")
            raise RuntimeError(f"codex exec failed (exit {res.returncode}); see {log_path}")


def _run_codex_chunks(
    *,
    chunks: list[CodexChunkPaths],
    model: str | None,
    target_language: str,
    max_workers: int,
    prompt_mode: str,
) -> None:
    if not chunks:
        return

    workers = max(1, max_workers)
    pending = list(chunks)
    while pending:
        if workers == 1 or len(pending) == 1:
            for ch in pending:
                run_codex_chunk(
                    chunk=ch, model=model, target_language=target_language, prompt_mode=prompt_mode
                )
            return

        failed: list[tuple[CodexChunkPaths, Exception]] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {
                ex.submit(
                    run_codex_chunk,
                    chunk=ch,
                    model=model,
                    target_language=target_language,
                    prompt_mode=prompt_mode,
                ): ch
                for ch in pending
            }
            for fut in as_completed(fut_map):
                ch = fut_map[fut]
                try:
                    fut.result()
                except Exception as e:
                    failed.append((ch, e if isinstance(e, Exception) else RuntimeError(str(e))))

        if not failed:
            return

        rate_limited = any("rate limited" in str(err).lower() for _, err in failed)
        if rate_limited and workers > 2:
            workers = 2
            pending = [ch for ch, _ in failed]
            debug(
                f"codex:{target_language.lower()}: backing off parallel chunks to workers=2 "
                f"after rate limit; retrying {len(pending)} chunk(s)"
            )
            continue

        raise failed[0][1]


def translate_es_with_codex(
    *,
    cues: list[tuple[str, str]],
    base_path: Path,
    chunk_size_cues: int,
    model: str | None,
    resume: bool,
    target_language: str,
    io_tag: str,
    max_workers: int,
    prompt_mode: str = "translate",
) -> dict[str, str]:
    """
    Returns id->translated_text map for all cues provided.
    """
    chunks = chunk_cues(cues, chunk_cues=chunk_size_cues, base_path=base_path, io_tag=io_tag)

    # Run missing chunks.
    pending: list[CodexChunkPaths] = []
    for ch in chunks:
        # Treat 0-byte outputs as invalid; they can be left behind by failed runs.
        if resume and ch.out_jsonl.exists() and ch.out_jsonl.stat().st_size > 0:
            continue
        pending.append(ch)
    _run_codex_chunks(
        chunks=pending,
        model=model,
        target_language=target_language,
        max_workers=max_workers,
        prompt_mode=prompt_mode,
    )

    # Merge.
    merged: dict[str, str] = {}
    for ch in chunks:
        if not ch.out_jsonl.exists():
            raise RuntimeError(f"missing codex output chunk: {ch.out_jsonl}")
        merged.update(_parse_jsonl_map(ch.out_jsonl))

    # Validate completeness + retry missing ids if necessary.
    want = {i for i, _ in cues}
    missing = sorted(list(want - set(merged.keys())))
    if missing:
        debug(f"codex output missing {len(missing)} ids; retrying (example: {missing[:5]})")
        attempt = 1
        for sz in [min(50, chunk_size_cues), 10, 1]:
            if not missing:
                break
            missing_set = set(missing)
            missing_cues = [(i, t) for i, t in cues if i in missing_set]
            retry_base = Path(str(base_path) + f".retry{attempt}")
            retry_chunks = chunk_cues(missing_cues, chunk_cues=sz, base_path=retry_base, io_tag=io_tag)
            _run_codex_chunks(
                chunks=retry_chunks,
                model=model,
                target_language=target_language,
                max_workers=max_workers,
                prompt_mode=prompt_mode,
            )
            for ch in retry_chunks:
                merged.update(_parse_jsonl_map(ch.out_jsonl))
            missing = sorted(list(want - set(merged.keys())))
            attempt += 1

    if missing:
        raise RuntimeError(f"codex output missing {len(missing)} ids after retries (example: {missing[:5]})")
    return merged
