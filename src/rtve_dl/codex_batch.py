from __future__ import annotations

import base64
import hashlib
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
from rtve_dl.global_phrase_cache import normalize_es_text

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
_LEADING_PUNCT_RE = re.compile(r"^[\s\-\u2013\u2014\.\,\!\?\:;\"'“”‘’\(\)\[\]\{\}]+")
_NOCHUNK_CACHE_FORMAT = 2
_PROMPT_FILES = {
    "translate_ru": "ru_full.md",
    "translate_en": "en_mt.md",
    "ru_refs_b2plus": "ru_refs.md",
    "es_clean_light": "es_clean.md",
}
_TOKENS_USED_RE = re.compile(r"tokens used\s*\n\s*([0-9][0-9,]*)", re.IGNORECASE | re.MULTILINE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_codex_on_path() -> None:
    if subprocess.run(["codex", "--version"], capture_output=True, text=True).returncode != 0:
        raise RuntimeError("codex CLI not found on PATH")


def _ensure_claude_on_path() -> None:
    if subprocess.run(["claude", "--version"], capture_output=True, text=True).returncode != 0:
        raise RuntimeError("claude CLI not found on PATH")


def _resolve_claude_model(model: str | None) -> str:
    """Pass through model name to Claude CLI (accepts aliases like 'sonnet', 'opus')."""
    return model or "sonnet"


def _load_prompt_template(prompt_mode: str) -> str:
    file_name = _PROMPT_FILES.get(prompt_mode)
    if not file_name:
        raise RuntimeError(f"unknown prompt mode: {prompt_mode}")
    # Read directly from source tree (next to this file)
    prompt_dir = Path(__file__).parent / "prompts"
    prompt_path = prompt_dir / file_name
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    # Fallback to installed package resources
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


def _strip_leading_punct(text: str) -> str:
    return _LEADING_PUNCT_RE.sub("", text or "")


def _make_echo(text: str) -> str:
    norm = normalize_es_text(text)
    norm = _strip_leading_punct(norm)
    return norm[:16].strip()


def _model_id(cue_id: str, text: str) -> str:
    raw = f"{cue_id}|{text}".encode("utf-8", errors="replace")
    digest = hashlib.sha256(raw).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:8]


def _build_expected_map(cues: list[tuple[str, str]]) -> dict[str, tuple[str, str]]:
    expected: dict[str, tuple[str, str]] = {}
    for cue_id, text in cues:
        mid = _model_id(cue_id, text)
        expected[mid] = (cue_id, _make_echo(text))
    return expected


def _parse_tsv_map(path: Path, *, allow_id_only: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        row = line.rstrip("\r")
        if not row.strip():
            continue
        parts = row.split("\t")
        if len(parts) < 2:
            if allow_id_only and len(parts) == 1:
                cue_id = _tsv_unescape(parts[0]).strip()
                if cue_id:
                    out[cue_id] = ""
            continue
        cue_id = _tsv_unescape(parts[0]).strip()
        # Some model outputs include stray extra tabs in value; treat everything
        # after id as the value instead of truncating at the first tab.
        text = _tsv_unescape("\t".join(parts[1:]))
        if cue_id:
            out[cue_id] = text
    return out


def _allow_id_only_rows(target_language: str) -> bool:
    # ru_refs prompt can legitimately produce "id" with empty gloss value.
    # We accept that as id<TAB>"" for better robustness with mini model.
    return target_language.strip().lower() == "russianrefs"


def _parse_codex_tsv_output(path: Path, *, target_language: str) -> dict[str, str]:
    out = _parse_tsv_map(path, allow_id_only=_allow_id_only_rows(target_language))
    return out


def _parse_tsv_with_echo(
    path: Path,
    *,
    expected: dict[str, tuple[str, str]],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        row = line.rstrip("\r")
        if not row.strip():
            continue
        parts = row.split("\t")
        if len(parts) < 3:
            continue
        model_id = _tsv_unescape(parts[0]).strip()
        echo = _tsv_unescape(parts[-1]).strip()
        text = _tsv_unescape(parts[1]).strip()
        expected_row = expected.get(model_id)
        if not expected_row:
            continue
        _cue_id, expected_echo = expected_row
        if echo != expected_echo:
            continue
        out[model_id] = text
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


def _write_nochunk_cache(path: Path, mapping: dict[str, str]) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"format": _NOCHUNK_CACHE_FORMAT}}) + "\n")
        for cue_id, text in mapping.items():
            f.write(json.dumps({"id": cue_id, "text": text}, ensure_ascii=False) + "\n")


def _is_nochunk_cache_compatible(path: Path) -> bool:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            meta = obj.get("_meta") if isinstance(obj, dict) else None
            if isinstance(meta, dict):
                return int(meta.get("format", 0) or 0) == _NOCHUNK_CACHE_FORMAT
            return False
    except Exception:
        return False
    return False


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
    use_context: bool = True,
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
                model_id = _model_id(cue_id, text)
                echo = _make_echo(text)
                f_jsonl.write(json.dumps({"id": cue_id, "text": text}, ensure_ascii=False) + "\n")
                if use_context:
                    global_idx = i + j
                    left = cues[global_idx - 1][1] if global_idx > 0 else ""
                    right = cues[global_idx + 1][1] if (global_idx + 1) < len(cues) else ""
                    f_tsv.write(
                        "\t".join(
                            [
                                _tsv_escape(model_id),
                                _tsv_escape(text),
                                _tsv_escape(left),
                                _tsv_escape(right),
                                _tsv_escape(echo),
                            ]
                        )
                        + "\n"
                    )
                else:
                    f_tsv.write(
                        "\t".join([_tsv_escape(model_id), _tsv_escape(text), _tsv_escape(echo)]) + "\n"
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
    backend: str = "claude",
) -> None:
    payload = chunk.in_tsv.read_text(encoding="utf-8")
    prompt = _build_prompt(tsv_payload=payload, prompt_mode=prompt_mode)

    if backend == "claude":
        _ensure_claude_on_path()
        resolved_model = _resolve_claude_model(model)
        # --setting-sources user: skip project context (CLAUDE.md) to avoid Claude acting as code assistant
        cmd = ["claude", "-p", "--print", "--model", resolved_model, "--setting-sources", "user"]
        debug("claude: " + " ".join(cmd))
    else:
        _ensure_codex_on_path()
        cmd = ["codex", "exec", "-s", "read-only", "--output-last-message", str(chunk.out_tsv)]
        if model:
            cmd += ["-m", model]
        cmd.append("-")
        debug("codex exec: " + " ".join(cmd))

    with stage(f"{backend}:{target_language.lower()}:chunk:{chunk.out_jsonl.name}"):
        started_at = _now_iso()
        t0 = time.time()
        res = subprocess.run(cmd, input=prompt, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        # For Claude backend, write stdout to out_tsv (codex writes directly via --output-last-message)
        if backend == "claude" and res.returncode == 0:
            chunk.out_tsv.write_text(res.stdout or "", encoding="utf-8")

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
            # Auth error detection for both backends
            if backend == "claude":
                if (
                    "api key" in out
                    or "unauthorized" in out
                    or "authentication" in out
                    or "invalid_api_key" in out
                ):
                    raise RuntimeError(
                        f"claude failed due to auth error. Check ANTHROPIC_API_KEY. Details: {log_path}"
                    )
            else:
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
                raise RuntimeError(f"{backend} rate limited; see {log_path}")
            raise RuntimeError(f"{backend} failed (exit {res.returncode}); see {log_path}")

        expected = _build_expected_map(
            [
                (str(obj.get("id", "")), obj.get("text", ""))
                for obj in (
                    json.loads(line)
                    for line in chunk.in_jsonl.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.strip()
                )
                if isinstance(obj, dict) and isinstance(obj.get("text"), str)
            ]
        )
        parsed = _parse_tsv_with_echo(chunk.out_tsv, expected=expected)
        if not parsed:
            log_path = Path(str(chunk.out_jsonl) + ".log")
            raw_out = chunk.out_tsv.read_text(encoding="utf-8", errors="replace")
            log_path.write_text(
                f"{backend} returned empty/unparseable output\n\n----raw output----\n" + raw_out,
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
            raise RuntimeError(f"{backend} empty/unparseable output; see {log_path}")
        remapped: dict[str, str] = {}
        for model_id, text in parsed.items():
            expected_row = expected.get(model_id)
            if not expected_row:
                continue
            cue_id, _echo = expected_row
            remapped[cue_id] = text
        _write_jsonl_map(chunk.out_jsonl, remapped)
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
    backend: str = "claude",
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
                        backend=backend,
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
                        backend=backend,
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
                f"{backend}:{target_language.lower()}: first-pass failed for {len(failed)} chunk(s), "
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
                f"{backend}:{target_language.lower()}: backing off parallel chunks to workers=2 "
                f"after rate limit; retrying {len(pending)} chunk(s)"
            )
            continue

        raise failed[0][1]


def _translate_es_chunked(
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
    use_context: bool = True,
    backend: str = "claude",
) -> dict[str, str]:
    """Chunked translation mode: split cues into batches and process in parallel."""
    chunks = chunk_cues(cues, chunk_cues=chunk_size_cues, base_path=base_path, io_tag=io_tag, use_context=use_context)

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
        backend=backend,
    )

    merged: dict[str, str] = {}
    for ch in chunks:
        if not ch.out_jsonl.exists():
            raise RuntimeError(f"missing {backend} output chunk: {ch.out_jsonl}")
        merged.update(_parse_jsonl_map(ch.out_jsonl))

    want = {i for i, _ in cues}
    missing = sorted(list(want - set(merged.keys())))
    if missing:
        debug(f"{backend} output missing {len(missing)} ids; retrying (example: {missing[:5]})")
        attempt = 1
        for sz in [min(50, chunk_size_cues), 10, 1]:
            if not missing:
                break
            missing_set = set(missing)
            missing_cues = [(i, t) for i, t in cues if i in missing_set]
            retry_base = Path(str(base_path) + f".retry{attempt}")
            retry_chunks = chunk_cues(missing_cues, chunk_cues=sz, base_path=retry_base, io_tag=io_tag, use_context=use_context)
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
                backend=backend,
            )
            for ch in retry_chunks:
                merged.update(_parse_jsonl_map(ch.out_jsonl))
            missing = sorted(list(want - set(merged.keys())))
            attempt += 1

    if missing:
        raise RuntimeError(f"{backend} output missing {len(missing)} ids after retries (example: {missing[:5]})")
    return merged


def _translate_no_chunk(
    *,
    cues: list[tuple[str, str]],
    base_path: Path,
    model: str | None,
    target_language: str,
    io_tag: str,
    prompt_mode: str,
    context: CodexExecutionContext | None = None,
    backend: str = "claude",
    use_context: bool = True,
) -> dict[str, str]:
    """No-chunk translation mode: send all cues in a single request."""
    # Cache file for resume
    cache_file = Path(str(base_path) + f".{io_tag}.nochunk.out.jsonl")
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    # Check for cached result (full or partial)
    want = {i for i, _ in cues}
    parsed: dict[str, str] = {}
    skip_main_request = False

    if cache_file.exists() and cache_file.stat().st_size > 0:
        if not _is_nochunk_cache_compatible(cache_file):
            debug(f"{backend}:{target_language.lower()}:nochunk cache format mismatch, ignoring: {cache_file}")
        else:
            cached = _parse_jsonl_map(cache_file)
            cached_ids = set(cached.keys())
            if want <= cached_ids:
                # Full cache hit
                debug(f"{backend}:{target_language.lower()}:nochunk cache hit: {cache_file}")
                return {k: v for k, v in cached.items() if k in want}
            elif cached_ids & want:
                # Partial cache hit - skip main request, go straight to retry
                parsed = {k: v for k, v in cached.items() if k in want}
                have_count = len(parsed)
                want_count = len(want)
                debug(f"{backend}:{target_language.lower()}:nochunk partial cache ({have_count}/{want_count}), skipping main request")
                skip_main_request = True

    if not skip_main_request:
        # Build TSV payload (include context columns when requested)
        tsv_lines = []
        for idx, (cue_id, text) in enumerate(cues):
            model_id = _model_id(cue_id, text)
            echo = _make_echo(text)
            if use_context:
                prev_text = cues[idx - 1][1] if idx > 0 else ""
                next_text = cues[idx + 1][1] if idx < len(cues) - 1 else ""
                tsv_lines.append(
                    "\t".join(
                        [
                            _tsv_escape(model_id),
                            _tsv_escape(text),
                            _tsv_escape(prev_text),
                            _tsv_escape(next_text),
                            _tsv_escape(echo),
                        ]
                    )
                )
            else:
                tsv_lines.append(
                    "\t".join([_tsv_escape(model_id), _tsv_escape(text), _tsv_escape(echo)])
                )
        tsv_payload = "\n".join(tsv_lines)

        prompt = _build_prompt(tsv_payload=tsv_payload, prompt_mode=prompt_mode)

        # Output file for raw TSV response
        out_tsv = Path(str(base_path) + f".{io_tag}.nochunk.out.tsv")

        if backend == "claude":
            _ensure_claude_on_path()
            resolved_model = _resolve_claude_model(model)
            # --setting-sources user: skip project context (CLAUDE.md) to avoid Claude acting as code assistant
            cmd = ["claude", "-p", "--print", "--model", resolved_model, "--setting-sources", "user"]
            debug("claude (no-chunk): " + " ".join(cmd))
        else:
            _ensure_codex_on_path()
            cmd = ["codex", "exec", "-s", "read-only", "--output-last-message", str(out_tsv)]
            if model:
                cmd += ["-m", model]
            cmd.append("-")
            debug("codex exec (no-chunk): " + " ".join(cmd))

        with stage(f"{backend}:{target_language.lower()}:nochunk:{len(cues)} cues"):
            started_at = _now_iso()
            t0 = time.time()
            res = subprocess.run(cmd, input=prompt, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            # For Claude backend, write stdout to out_tsv
            if backend == "claude" and res.returncode == 0:
                out_tsv.write_text(res.stdout or "", encoding="utf-8")

            total_tokens = _parse_total_tokens(res.stdout or "")

            if res.returncode != 0:
                log_path = Path(str(cache_file) + ".log")
                log_path.write_text(res.stdout or "", encoding="utf-8", errors="replace")
                out = (res.stdout or "").lower()
                if backend == "claude":
                    if (
                        "api key" in out
                        or "unauthorized" in out
                        or "authentication" in out
                        or "invalid_api_key" in out
                    ):
                        raise RuntimeError(
                            f"claude failed due to auth error. Check ANTHROPIC_API_KEY. Details: {log_path}"
                        )
                else:
                    if (
                        "401 unauthorized" in out
                        or "provided authentication token is expired" in out
                        or "refresh_token_reused" in out
                    ):
                        raise RuntimeError(
                            "codex exec failed due to expired/invalid auth. "
                            "Run `codex logout` then `codex login --device-auth` and retry. "
                            f"Details: {log_path}"
                        )
                if "429" in out or "rate limit" in out or "too many requests" in out:
                    raise RuntimeError(f"{backend} rate limited; see {log_path}")
                raise RuntimeError(f"{backend} failed (exit {res.returncode}); see {log_path}")

            expected = _build_expected_map(cues)
            parsed = _parse_tsv_with_echo(out_tsv, expected=expected)
            if not parsed:
                log_path = Path(str(cache_file) + ".log")
                raw_out = out_tsv.read_text(encoding="utf-8", errors="replace")
                log_path.write_text(
                    f"{backend} returned empty/unparseable output\n\n----raw output----\n" + raw_out,
                    encoding="utf-8",
                    errors="replace",
                )
                raise RuntimeError(f"{backend} empty/unparseable output; see {log_path}")

            # Record telemetry
            if context is not None and context.telemetry is not None and context.run_id and context.episode_id:
                context.telemetry.record_codex_chunk(
                    run_id=context.run_id,
                    episode_id=context.episode_id,
                    track_type=context.track_type,
                    chunk_name=cache_file.name,
                    model=model,
                    chunk_size=len(cues),
                    input_items=len(cues),
                    started_at=started_at,
                    ended_at=_now_iso(),
                    duration_ms=int((time.time() - t0) * 1000),
                    ok=True,
                    exit_code=0,
                    missing_ids=0,
                    fallback_used=False,
                    log_path=None,
                    total_tokens=total_tokens,
                    usage_source="stdout_tokens_used" if total_tokens else "missing",
                    usage_parse_ok=total_tokens is not None,
                )

            # Write cache for resume (remap model_id -> numeric id)
            remapped: dict[str, str] = {}
            for model_id, text in parsed.items():
                expected_row = expected.get(model_id)
                if not expected_row:
                    continue
                cue_id, _echo = expected_row
                remapped[cue_id] = text
            _write_nochunk_cache(cache_file, remapped)

    # Validate output and retry missing IDs with chunked mode
    if not skip_main_request:
        parsed = remapped
    missing = sorted(list(want - set(parsed.keys())), key=lambda x: int(x) if x.isdigit() else 0)

    if missing:
        debug(f"{backend}:{target_language.lower()}:nochunk missing {len(missing)} ids, retrying with chunked mode")

        # Build context-aware cues for missing IDs
        cue_map = {cid: text for cid, text in cues}
        cue_ids = [cid for cid, _ in cues]
        id_to_idx = {cid: idx for idx, cid in enumerate(cue_ids)}

        # Retry using chunked mode with context
        retry_base = Path(str(base_path) + ".nochunk_retry")
        retry_ctx = None
        if context is not None:
            retry_ctx = CodexExecutionContext(
                telemetry=context.telemetry,
                run_id=context.run_id,
                episode_id=context.episode_id,
                track_type=context.track_type + "_retry",
                chunk_size=min(50, len(missing)),
            )

        # Use small chunk sizes for retry
        for retry_sz in [min(50, len(missing)), 10, 1]:
            if not missing:
                break
            retry_cues = [(mid, cue_map[mid]) for mid in missing]
            retry_chunks = chunk_cues(
                retry_cues,
                chunk_cues=retry_sz,
                base_path=retry_base,
                io_tag=io_tag + "_retry",
                use_context=use_context,
            )
            if use_context:
                # Build context for retry chunks from original cue list
                for ch in retry_chunks:
                    # Rewrite TSV with proper context from full cue list
                    with ch.in_tsv.open("w", encoding="utf-8") as f_tsv:
                        for line in ch.in_jsonl.read_text(encoding="utf-8").splitlines():
                            if not line.strip():
                                continue
                            try:
                                obj = json.loads(line)
                                cue_id = str(obj.get("id", ""))
                                text = obj.get("text", "")
                                idx = id_to_idx.get(cue_id)
                                if idx is not None:
                                    prev_text = cue_map.get(cue_ids[idx - 1], "") if idx > 0 else ""
                                    next_text = cue_map.get(cue_ids[idx + 1], "") if idx < len(cue_ids) - 1 else ""
                                else:
                                    prev_text = ""
                                    next_text = ""
                                model_id = _model_id(cue_id, text)
                                echo = _make_echo(text)
                                f_tsv.write(
                                    "\t".join(
                                        [
                                            _tsv_escape(model_id),
                                            _tsv_escape(text),
                                            _tsv_escape(prev_text),
                                            _tsv_escape(next_text),
                                            _tsv_escape(echo),
                                        ]
                                    )
                                    + "\n"
                                )
                            except Exception:
                                continue

            _run_codex_chunks(
                chunks=retry_chunks,
                model=model,
                fallback_model=None,
                target_language=target_language,
                max_workers=1,
                prompt_mode=prompt_mode,
                context=retry_ctx,
                backend=backend,
            )
            for ch in retry_chunks:
                if ch.out_jsonl.exists():
                    parsed.update(_parse_jsonl_map(ch.out_jsonl))
            missing = sorted(list(want - set(parsed.keys())), key=lambda x: int(x) if x.isdigit() else 0)

        # Update cache with retry results
        _write_nochunk_cache(cache_file, parsed)

        if missing:
            raise RuntimeError(
                f"{backend} no-chunk output still missing {len(missing)} ids after retry (example: {missing[:5]})"
            )

    return parsed


def translate_es(
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
    use_context: bool = True,
    backend: str = "claude",
    no_chunk: bool | None = None,
) -> dict[str, str]:
    """
    Unified translation entry point.

    Dispatches to no-chunk or chunked mode based on backend and flags:
    - no_chunk=True: Single request with full context (default for Claude)
    - no_chunk=False: Chunked parallel batches (default for Codex)
    - no_chunk=None: Auto-select based on backend
    """
    # Default: no_chunk=True for claude, False for codex
    if no_chunk is None:
        no_chunk = (backend == "claude")

    if no_chunk:
        return _translate_no_chunk(
            cues=cues,
            base_path=base_path,
            model=model,
            target_language=target_language,
            io_tag=io_tag,
            prompt_mode=prompt_mode,
            context=context,
            backend=backend,
            use_context=use_context or (prompt_mode == "es_clean_light"),
        )
    else:
        return _translate_es_chunked(
            cues=cues,
            base_path=base_path,
            chunk_size_cues=chunk_size_cues,
            model=model,
            fallback_model=fallback_model,
            resume=resume,
            target_language=target_language,
            io_tag=io_tag,
            max_workers=max_workers,
            prompt_mode=prompt_mode,
            context=context,
            use_context=use_context,
            backend=backend,
        )


# Backwards compatibility alias
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
    use_context: bool = True,
    backend: str = "claude",
    no_chunk: bool | None = None,
) -> dict[str, str]:
    """Legacy alias for translate_es()."""
    return translate_es(
        cues=cues,
        base_path=base_path,
        chunk_size_cues=chunk_size_cues,
        model=model,
        fallback_model=fallback_model,
        resume=resume,
        target_language=target_language,
        io_tag=io_tag,
        max_workers=max_workers,
        prompt_mode=prompt_mode,
        context=context,
        use_context=use_context,
        backend=backend,
        no_chunk=no_chunk,
    )
