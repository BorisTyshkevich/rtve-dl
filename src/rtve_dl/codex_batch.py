from __future__ import annotations

import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from rtve_dl.log import debug, stage

if TYPE_CHECKING:
    from rtve_dl.telemetry import TelemetryDB


@dataclass(frozen=True)
class CodexChunkPaths:
    in_jsonl: Path
    out_jsonl: Path
    in_tsv: Path
    out_tsv: Path


@dataclass(frozen=True)
class CodexExecutionContext:
    telemetry: TelemetryDB | None
    run_id: str | None
    episode_id: str | None
    track_type: str
    chunk_size: int


_JSON_LINE_RE = re.compile(r"^\s*\{.*\}\s*$")
_PROMPT_FILES = {
    "translate_ru": "ru_full.md",
    "translate_en": "en_mt.md",
    "ru_refs_b2plus": "ru_refs.md",
}
_TOKENS_USED_RE = re.compile(r"tokens used\s*\n\s*([0-9][0-9,]*)", re.IGNORECASE | re.MULTILINE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_codex_on_path() -> None:
    if subprocess.run(["codex", "--version"], capture_output=True, text=True).returncode != 0:
        raise RuntimeError("codex CLI not found on PATH")


def _load_prompt_template(prompt_mode: str) -> str:
    file_name = _PROMPT_FILES.get(prompt_mode)
    if not file_name:
        raise RuntimeError(f"unknown prompt mode: {prompt_mode}")
    return resources.files("rtve_dl.prompts").joinpath(file_name).read_text(encoding="utf-8")


def _build_prompt(*, tsv_payload: str, prompt_mode: str) -> str:
    template = _load_prompt_template(prompt_mode)
    return template.replace("{{PAYLOAD}}", tsv_payload)


def _tsv_escape(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def _tsv_unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    s = value or ""
    while i < len(s):
        ch = s[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(s):
            out.append("\\")
            i += 1
            continue
        nxt = s[i + 1]
        if nxt == "n":
            out.append("\n")
        elif nxt == "t":
            out.append("\t")
        elif nxt == "\\":
            out.append("\\")
        else:
            out.append(nxt)
        i += 2
    return "".join(out)


def _parse_tsv_map(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        row = line.rstrip("\r")
        if not row.strip():
            continue
        parts = row.split("\t")
        if len(parts) < 2:
            continue
        cue_id = _tsv_unescape(parts[0]).strip()
        text = _tsv_unescape(parts[1])
        if cue_id:
            out[cue_id] = text
    return out


def _parse_jsonl_map(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or not _JSON_LINE_RE.match(line):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        cue_id = str(obj.get("id", "")).strip()
        text = obj.get("text")
        if cue_id and isinstance(text, str):
            out[cue_id] = text
    return out


def _write_jsonl_map(path: Path, mapping: dict[str, str]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for cue_id, text in mapping.items():
            f.write(json.dumps({"id": cue_id, "text": text}, ensure_ascii=False) + "\n")


def _telemetry_record(
    *,
    context: CodexExecutionContext | None,
    chunk: CodexChunkPaths,
    model: str | None,
    started_at: str,
    t0: float,
    ok: bool,
    exit_code: int | None,
    fallback_used: bool,
    log_path: Path | None,
    total_tokens: int | None,
    usage_source: str,
    usage_parse_ok: bool,
) -> None:
    if context is None or context.telemetry is None or not context.run_id or not context.episode_id:
        return
    input_items = 0
    try:
        input_items = sum(1 for x in chunk.in_jsonl.read_text(encoding="utf-8").splitlines() if x.strip())
    except Exception:
        input_items = 0
    context.telemetry.record_codex_chunk(
        run_id=context.run_id,
        episode_id=context.episode_id,
        track_type=context.track_type,
        chunk_name=chunk.out_jsonl.name,
        model=model,
        chunk_size=context.chunk_size,
        input_items=input_items,
        started_at=started_at,
        ended_at=_now_iso(),
        duration_ms=int((time.time() - t0) * 1000),
        ok=ok,
        exit_code=exit_code,
        missing_ids=0,
        fallback_used=fallback_used,
        log_path=str(log_path) if log_path is not None else None,
        total_tokens=total_tokens,
        usage_source=usage_source,
        usage_parse_ok=usage_parse_ok,
    )


def _parse_total_tokens(raw: str) -> int | None:
    if not raw:
        return None
    matches = _TOKENS_USED_RE.findall(raw)
    if not matches:
        return None
    try:
        return int(matches[-1].replace(",", ""))
    except ValueError:
        return None


def chunk_cues(
    cues: list[tuple[str, str]],
    *,
    chunk_cues: int,
    base_path: Path,
    io_tag: str,
) -> list[CodexChunkPaths]:
    if chunk_cues <= 0:
        raise ValueError("chunk_cues must be positive")
    if not io_tag or not io_tag.isascii():
        raise ValueError("io_tag must be a non-empty ASCII string")

    stem = str(base_path) + f".c{chunk_cues}"

    out: list[CodexChunkPaths] = []
    for i in range(0, len(cues), chunk_cues):
        part = cues[i : i + chunk_cues]
        idx = (i // chunk_cues) + 1
        in_path = Path(stem + f".{io_tag}.in.{idx:04d}.jsonl")
        out_path = Path(stem + f".{io_tag}.out.{idx:04d}.jsonl")
        in_tsv = Path(stem + f".{io_tag}.in.{idx:04d}.tsv")
        out_tsv = Path(stem + f".{io_tag}.out.{idx:04d}.tsv")
        out.append(CodexChunkPaths(in_jsonl=in_path, out_jsonl=out_path, in_tsv=in_tsv, out_tsv=out_tsv))

        in_path.parent.mkdir(parents=True, exist_ok=True)
        with in_path.open("w", encoding="utf-8") as f_jsonl, in_tsv.open("w", encoding="utf-8") as f_tsv:
            for j, (cue_id, text) in enumerate(part):
                f_jsonl.write(json.dumps({"id": cue_id, "text": text}, ensure_ascii=False) + "\n")
                global_idx = i + j
                left = cues[global_idx - 1][1] if global_idx > 0 else ""
                right = cues[global_idx + 1][1] if (global_idx + 1) < len(cues) else ""
                f_tsv.write(
                    "\t".join(
                        [
                            _tsv_escape(cue_id),
                            _tsv_escape(text),
                            _tsv_escape(left),
                            _tsv_escape(right),
                        ]
                    )
                    + "\n"
                )
    return out


def run_codex_chunk(
    *,
    chunk: CodexChunkPaths,
    model: str | None,
    target_language: str,
    prompt_mode: str,
    context: CodexExecutionContext | None = None,
    fallback_used: bool = False,
) -> None:
    _ensure_codex_on_path()
    payload = chunk.in_tsv.read_text(encoding="utf-8")
    prompt = _build_prompt(tsv_payload=payload, prompt_mode=prompt_mode)

    cmd = ["codex", "exec", "-s", "read-only", "--output-last-message", str(chunk.out_tsv)]
    if model:
        cmd += ["-m", model]
    cmd.append("-")

    debug("codex exec: " + " ".join(cmd))
    with stage(f"codex:{target_language.lower()}:chunk:{chunk.out_jsonl.name}"):
        started_at = _now_iso()
        t0 = time.time()
        res = subprocess.run(cmd, input=prompt, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        total_tokens = _parse_total_tokens(res.stdout or "")
        usage_source = "stdout_tokens_used" if total_tokens is not None else "missing"
        usage_parse_ok = total_tokens is not None
        if res.returncode != 0:
            log_path = Path(str(chunk.out_jsonl) + ".log")
            log_path.write_text(res.stdout or "", encoding="utf-8", errors="replace")
            if total_tokens is None:
                total_tokens = _parse_total_tokens(log_path.read_text(encoding="utf-8", errors="replace"))
                if total_tokens is not None:
                    usage_source = "log_tokens_used"
                    usage_parse_ok = True
            try:
                if chunk.out_jsonl.exists() and chunk.out_jsonl.stat().st_size == 0:
                    chunk.out_jsonl.unlink()
            except OSError:
                pass
            _telemetry_record(
                context=context,
                chunk=chunk,
                model=model,
                started_at=started_at,
                t0=t0,
                ok=False,
                exit_code=res.returncode,
                fallback_used=fallback_used,
                log_path=log_path,
                total_tokens=total_tokens,
                usage_source=usage_source,
                usage_parse_ok=usage_parse_ok,
            )
            out = (res.stdout or "").lower()
            if (
                "401 unauthorized" in out
                or "provided authentication token is expired" in out
                or "refresh_token_reused" in out
            ):
                raise RuntimeError(
                    "codex exec failed due to expired/invalid auth. "
                    "Run `codex logout` then `codex login --device-auth` (or `printenv OPENAI_API_KEY | codex login --with-api-key`) "
                    f"and retry. Details: {log_path}"
                )
            if "429" in out or "rate limit" in out or "too many requests" in out:
                raise RuntimeError(f"codex rate limited; see {log_path}")
            raise RuntimeError(f"codex exec failed (exit {res.returncode}); see {log_path}")

        parsed = _parse_tsv_map(chunk.out_tsv)
        if not parsed:
            parsed = _parse_jsonl_map(chunk.out_tsv)
        if not parsed:
            log_path = Path(str(chunk.out_jsonl) + ".log")
            raw_out = chunk.out_tsv.read_text(encoding="utf-8", errors="replace")
            log_path.write_text(
                "codex returned empty/unparseable output\n\n----raw output----\n" + raw_out,
                encoding="utf-8",
                errors="replace",
            )
            _telemetry_record(
                context=context,
                chunk=chunk,
                model=model,
                started_at=started_at,
                t0=t0,
                ok=False,
                exit_code=0,
                fallback_used=fallback_used,
                log_path=log_path,
                total_tokens=total_tokens,
                usage_source=usage_source,
                usage_parse_ok=usage_parse_ok,
            )
            raise RuntimeError(f"codex empty/unparseable output; see {log_path}")
        _write_jsonl_map(chunk.out_jsonl, parsed)
        _telemetry_record(
            context=context,
            chunk=chunk,
            model=model,
            started_at=started_at,
            t0=t0,
            ok=True,
            exit_code=0,
            fallback_used=fallback_used,
            log_path=None,
            total_tokens=total_tokens,
            usage_source=usage_source,
            usage_parse_ok=usage_parse_ok,
        )


def _run_codex_chunks(
    *,
    chunks: list[CodexChunkPaths],
    model: str | None,
    fallback_model: str | None,
    target_language: str,
    max_workers: int,
    prompt_mode: str,
    context: CodexExecutionContext | None,
) -> None:
    if not chunks:
        return

    workers = max(1, max_workers)
    pending = list(chunks)
    fallback_done = False
    while pending:
        failed: list[tuple[CodexChunkPaths, Exception]] = []
        run_model = model
        use_fallback = False
        if fallback_done:
            run_model = fallback_model
            use_fallback = True

        if workers == 1 or len(pending) == 1:
            for ch in pending:
                try:
                    run_codex_chunk(
                        chunk=ch,
                        model=run_model,
                        target_language=target_language,
                        prompt_mode=prompt_mode,
                        context=context,
                        fallback_used=use_fallback,
                    )
                except Exception as e:
                    failed.append((ch, e if isinstance(e, Exception) else RuntimeError(str(e))))
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                fut_map = {
                    ex.submit(
                        run_codex_chunk,
                        chunk=ch,
                        model=run_model,
                        target_language=target_language,
                        prompt_mode=prompt_mode,
                        context=context,
                        fallback_used=use_fallback,
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

        if not fallback_done and fallback_model and fallback_model != model:
            debug(
                f"codex:{target_language.lower()}: mini first-pass failed for {len(failed)} chunk(s), "
                "retrying failed chunks with fallback model"
            )
            pending = [ch for ch, _ in failed]
            fallback_done = True
            continue

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
    fallback_model: str | None,
    resume: bool,
    target_language: str,
    io_tag: str,
    max_workers: int,
    prompt_mode: str = "translate_ru",
    context: CodexExecutionContext | None = None,
) -> dict[str, str]:
    chunks = chunk_cues(cues, chunk_cues=chunk_size_cues, base_path=base_path, io_tag=io_tag)

    pending: list[CodexChunkPaths] = []
    for ch in chunks:
        if resume and ch.out_jsonl.exists() and ch.out_jsonl.stat().st_size > 0:
            continue
        pending.append(ch)
    _run_codex_chunks(
        chunks=pending,
        model=model,
        fallback_model=fallback_model,
        target_language=target_language,
        max_workers=max_workers,
        prompt_mode=prompt_mode,
        context=context,
    )

    merged: dict[str, str] = {}
    for ch in chunks:
        if not ch.out_jsonl.exists():
            raise RuntimeError(f"missing codex output chunk: {ch.out_jsonl}")
        merged.update(_parse_jsonl_map(ch.out_jsonl))

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
            retry_ctx = context
            if retry_ctx is not None:
                retry_ctx = CodexExecutionContext(
                    telemetry=retry_ctx.telemetry,
                    run_id=retry_ctx.run_id,
                    episode_id=retry_ctx.episode_id,
                    track_type=retry_ctx.track_type,
                    chunk_size=sz,
                )
            _run_codex_chunks(
                chunks=retry_chunks,
                model=model,
                fallback_model=fallback_model,
                target_language=target_language,
                max_workers=max_workers,
                prompt_mode=prompt_mode,
                context=retry_ctx,
            )
            for ch in retry_chunks:
                merged.update(_parse_jsonl_map(ch.out_jsonl))
            missing = sorted(list(want - set(merged.keys())))
            attempt += 1

    if missing:
        raise RuntimeError(f"codex output missing {len(missing)} ids after retries (example: {missing[:5]})")
    return merged
