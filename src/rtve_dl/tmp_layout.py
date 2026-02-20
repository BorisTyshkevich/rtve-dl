from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rtve_dl.log import debug, error


@dataclass(frozen=True)
class TmpLayout:
    root: Path
    mp4: Path
    vtt: Path
    srt: Path
    codex_en: Path
    codex_es_clean: Path
    codex_ru: Path
    codex_ru_ref: Path
    codex_en_asr: Path
    codex_ru_asr: Path
    codex_ru_ref_asr: Path
    meta: Path
    meta_legacy: Path

    @classmethod
    def for_slug(cls, root: Path) -> "TmpLayout":
        return cls(
            root=root,
            mp4=root / "mp4",
            vtt=root / "vtt",
            srt=root / "srt",
            codex_en=root / "codex" / "en",
            codex_es_clean=root / "codex" / "es_clean",
            codex_ru=root / "codex" / "ru",
            codex_ru_ref=root / "codex" / "ru_ref",
            codex_en_asr=root / "codex" / "en_asr",
            codex_ru_asr=root / "codex" / "ru_asr",
            codex_ru_ref_asr=root / "codex" / "ru_ref_asr",
            meta=root / "meta",
            meta_legacy=root / "meta" / "legacy",
        )

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.mp4.mkdir(parents=True, exist_ok=True)
        self.vtt.mkdir(parents=True, exist_ok=True)
        self.srt.mkdir(parents=True, exist_ok=True)
        self.codex_en.mkdir(parents=True, exist_ok=True)
        self.codex_es_clean.mkdir(parents=True, exist_ok=True)
        self.codex_ru.mkdir(parents=True, exist_ok=True)
        self.codex_ru_ref.mkdir(parents=True, exist_ok=True)
        self.codex_en_asr.mkdir(parents=True, exist_ok=True)
        self.codex_ru_asr.mkdir(parents=True, exist_ok=True)
        self.codex_ru_ref_asr.mkdir(parents=True, exist_ok=True)
        self.meta.mkdir(parents=True, exist_ok=True)
        self.meta_legacy.mkdir(parents=True, exist_ok=True)

    def mp4_file(self, base: str) -> Path:
        return self.mp4 / f"{base}.mp4"

    def vtt_es_file(self, asset_id: str) -> Path:
        return self.vtt / f"{asset_id}.es.vtt"

    def vtt_en_file(self, asset_id: str) -> Path:
        return self.vtt / f"{asset_id}.en.vtt"

    def srt_es_file(self, base: str) -> Path:
        return self.srt / f"{base}.spa.srt"

    def srt_es_aligned_file(self, base: str) -> Path:
        return self.srt / f"{base}.spa.aligned.srt"

    def srt_en_file(self, base: str) -> Path:
        return self.srt / f"{base}.eng.srt"

    def srt_ru_file(self, base: str) -> Path:
        return self.srt / f"{base}.rus.srt"

    def srt_refs_file(self, base: str) -> Path:
        return self.srt / f"{base}.spa_rus.srt"

    def srt_bi_full_file(self, base: str) -> Path:
        return self.srt / f"{base}.spa_rus_full.srt"

    def srt_es_asr_file(self, base: str) -> Path:
        return self.srt / f"{base}.spa.asr.srt"

    def srt_en_asr_file(self, base: str) -> Path:
        return self.srt / f"{base}.eng.asr.srt"

    def srt_ru_asr_file(self, base: str) -> Path:
        return self.srt / f"{base}.rus.asr.srt"

    def srt_refs_asr_file(self, base: str) -> Path:
        return self.srt / f"{base}.spa_rus.asr.srt"

    def srt_bi_full_asr_file(self, base: str) -> Path:
        return self.srt / f"{base}.spa_rus_full.asr.srt"

    def codex_base(self, base: str, track: str) -> Path:
        if track == "en":
            return self.codex_en / f"{base}.en"
        if track == "es_clean":
            return self.codex_es_clean / f"{base}.es_clean"
        if track == "ru":
            return self.codex_ru / f"{base}.ru"
        if track == "ru_ref":
            return self.codex_ru_ref / f"{base}.ru_ref"
        if track == "en_asr":
            return self.codex_en_asr / f"{base}.en_asr"
        if track == "ru_asr":
            return self.codex_ru_asr / f"{base}.ru_asr"
        if track == "ru_ref_asr":
            return self.codex_ru_ref_asr / f"{base}.ru_ref_asr"
        raise ValueError(f"unknown codex track: {track}")

    def telemetry_db(self) -> Path:
        return self.meta / "telemetry.sqlite"

    def index_meta_ru_cache(self) -> Path:
        return self.meta / "index_meta_ru.json"


def _codex_track_for_name(name: str) -> str | None:
    if ".es_clean." in name:
        return "es_clean"
    if ".ru_ref." in name:
        return "ru_ref"
    if ".en." in name:
        return "en"
    if ".ru." in name or ".rus." in name:
        return "ru"
    return None


def _move_file(src: Path, dst: Path) -> None:
    if src == dst:
        return
    if dst.exists():
        try:
            if src.stat().st_size == dst.stat().st_size:
                src.unlink()
                debug(f"tmp:migrate dedup removed {src}")
                return
        except OSError:
            pass
        error(f"tmp:migrate collision keep-both: src={src} dst={dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    debug(f"tmp:migrate moved {src.name} -> {dst}")


def migrate_tmp_slug_layout(layout: TmpLayout) -> None:
    layout.ensure_dirs()
    for src in sorted(layout.root.glob("*")):
        if not src.is_file():
            continue
        n = src.name
        dst: Path | None = None

        if n.endswith(".mp4") or n.endswith(".mp4.partial.mp4"):
            dst = layout.mp4 / n
        elif n.endswith(".vtt"):
            dst = layout.vtt / n
        elif n.endswith(".srt") or ".srt.bak." in n:
            dst = layout.srt / n
        elif n.endswith(".srt.log"):
            dst = layout.meta_legacy / n
        elif (
            n in {"telemetry.sqlite", "index_meta_ru.json"}
            or n.startswith("catalog_")
        ):
            dst = layout.meta / n
        elif n.endswith(".jsonl") or n.endswith(".tsv") or n.endswith(".jsonl.log"):
            track = _codex_track_for_name(n)
            if track == "en":
                dst = layout.codex_en / n
            elif track == "es_clean":
                dst = layout.codex_es_clean / n
            elif track == "ru":
                dst = layout.codex_ru / n
            elif track == "ru_ref":
                dst = layout.codex_ru_ref / n
            else:
                dst = layout.meta_legacy / n
        elif n.endswith(".json") and n.startswith("index_meta_ru"):
            dst = layout.meta / n
        elif n in {".DS_Store"}:
            # macOS finder metadata; not useful in tmp cache.
            src.unlink(missing_ok=True)
            continue
        else:
            # Legacy/unknown artifacts (old scripts, schema snippets, logs).
            dst = layout.meta_legacy / n

        _move_file(src, dst)
