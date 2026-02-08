from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:80] if s else "series"


@dataclass(frozen=True)
class SeriesStore:
    series_slug: str
    root_dir: Path
    db_path: Path

    @staticmethod
    def open_or_create(*, series_url: str, series_slug: str | None) -> "SeriesStore":
        slug = series_slug or _slugify(series_url)
        root = Path("data") / "series" / slug
        root.mkdir(parents=True, exist_ok=True)
        db_path = root / "cache.sqlite3"
        store = SeriesStore(series_slug=slug, root_dir=root, db_path=db_path)
        store._init_db(series_url)
        store._init_lexicon_files()
        return store

    @staticmethod
    def open_existing(series_slug: str) -> "SeriesStore":
        root = Path("data") / "series" / series_slug
        if not root.exists():
            raise SystemExit(f"series not indexed: {root}")
        db_path = root / "cache.sqlite3"
        return SeriesStore(series_slug=series_slug, root_dir=root, db_path=db_path)

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self, series_url: str) -> None:
        con = self.connect()
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS meta(
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS assets(
                  asset_id TEXT PRIMARY KEY,
                  episode_url TEXT,
                  title TEXT,
                  season INTEGER,
                  episode INTEGER,
                  has_drm INTEGER,
                  subtitles_es_url TEXT,
                  subtitles_en_url TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS terms(
                  term TEXT NOT NULL,
                  kind TEXT NOT NULL CHECK(kind IN ('word','phrase')),
                  count INTEGER NOT NULL DEFAULT 0,
                  contexts_json TEXT NOT NULL DEFAULT '[]',
                  PRIMARY KEY(term, kind)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS gloss(
                  term TEXT NOT NULL,
                  kind TEXT NOT NULL CHECK(kind IN ('word','phrase')),
                  cefr TEXT NOT NULL,
                  skip INTEGER NOT NULL,
                  ru TEXT NOT NULL,
                  PRIMARY KEY(term, kind)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ru_cues(
                  cue_id TEXT PRIMARY KEY,
                  ru TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS cues(
                  cue_id TEXT PRIMARY KEY,
                  asset_id TEXT NOT NULL,
                  lang TEXT NOT NULL,
                  idx INTEGER NOT NULL,
                  start_ms INTEGER NOT NULL,
                  end_ms INTEGER NOT NULL,
                  text TEXT NOT NULL
                )
                """
            )
            con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('series_url', ?)", (series_url,))
            con.commit()
        finally:
            con.close()

    def _init_lexicon_files(self) -> None:
        stop = self.root_dir / "stopwords_es.txt"
        if not stop.exists():
            stop.write_text(
                "\n".join(
                    [
                        # Common function words / pronouns. Add/remove freely per your learning goals.
                        "a",
                        "al",
                        "algo",
                        "aquí",
                        "así",
                        "con",
                        "como",
                        "cómo",
                        "cuando",
                        "cuándo",
                        "de",
                        "del",
                        "desde",
                        "donde",
                        "dónde",
                        "el",
                        "ella",
                        "ellas",
                        "ellos",
                        "en",
                        "entre",
                        "era",
                        "eres",
                        "es",
                        "esa",
                        "esas",
                        "ese",
                        "eso",
                        "esos",
                        "esta",
                        "está",
                        "están",
                        "estas",
                        "este",
                        "esto",
                        "estos",
                        "estoy",
                        "fue",
                        "ha",
                        "han",
                        "hay",
                        "la",
                        "las",
                        "le",
                        "les",
                        "lo",
                        "los",
                        "me",
                        "mi",
                        "mis",
                        "mucho",
                        "muy",
                        "no",
                        "nos",
                        "nuestra",
                        "nuestro",
                        "o",
                        "os",
                        "para",
                        "pero",
                        "por",
                        "porque",
                        "que",
                        "qué",
                        "se",
                        "si",
                        "sí",
                        "sin",
                        "su",
                        "sus",
                        "te",
                        "tu",
                        "tus",
                        "un",
                        "una",
                        "uno",
                        "unos",
                        "unas",
                        "usted",
                        "ustedes",
                        "y",
                        "ya",
                        "yo",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

        for name in ("lexicon_words.tsv", "lexicon_phrases.tsv"):
            p = self.root_dir / name
            if not p.exists():
                p.write_text("term\tcefr\tskip\tru\n", encoding="utf-8")

    def load_stopwords(self) -> set[str]:
        p = self.root_dir / "stopwords_es.txt"
        if not p.exists():
            return set()
        out: set[str] = set()
        for line in p.read_text(encoding="utf-8").splitlines():
            w = line.strip().lower()
            if not w or w.startswith("#"):
                continue
            out.add(w)
        return out
