from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import re
from pathlib import Path
from urllib.parse import quote


_SEASON_EPISODE_RE = re.compile(r"\bS(\d{1,3})E(\d{1,3})\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Row:
    name: str
    size_bytes: int
    mtime: float
    season: int | None
    episode: int | None

def _parse_season_episode(name: str) -> tuple[int, int] | None:
    m = _SEASON_EPISODE_RE.search(name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    value = float(size_bytes)
    units = ["KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
    return f"{size_bytes} B"


def _format_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _mkv_rows(out_dir: Path) -> list[_Row]:
    rows: list[_Row] = []
    for p in sorted(out_dir.glob("*.mkv")):
        try:
            st = p.stat()
        except OSError:
            continue
        se = _parse_season_episode(p.name)
        rows.append(
            _Row(
                name=p.name,
                size_bytes=int(st.st_size),
                mtime=float(st.st_mtime),
                season=(se[0] if se else None),
                episode=(se[1] if se else None),
            )
        )

    def _key(r: _Row) -> tuple[int, int, int, str]:
        if r.season is not None and r.episode is not None:
            return (0, r.season, r.episode, r.name)
        return (1, 0, 0, r.name)

    rows.sort(key=_key)
    return rows


def build_slug_index(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _mkv_rows(out_dir)
    total_size = sum(r.size_bytes for r in rows)

    body_rows: list[str] = []
    for r in rows:
        name = escape(r.name)
        rel_href = quote(r.name)
        title_hint = escape(Path(r.name).stem, quote=True)
        body_rows.append(
            "<tr>"
            f"<td><a href=\"#\" class=\"m3u-generate\" data-media=\"{escape(rel_href, quote=True)}\" data-title=\"{title_hint}\">{name}</a></td>"
            f"<td class=\"mono\">{escape(_format_size(r.size_bytes))}</td>"
            f"<td class=\"mono\">{escape(_format_mtime(r.mtime))}</td>"
            f"<td><a href=\"{escape(rel_href, quote=True)}\" download>Download</a></td>"
            "</tr>"
        )

    if not body_rows:
        body_rows.append("<tr><td colspan=\"4\" class=\"empty\">No MKV files found.</td></tr>")

    title = f"RTVE Downloads: {out_dir.name}"
    rows_html = "\n        ".join(body_rows)

    html = (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\" />\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        f"  <title>{escape(title)}</title>\n"
        "  <style>\n"
        "    :root { color-scheme: light; }\n"
        "    body { margin: 24px; font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif; color: #1f2937; }\n"
        "    h1 { margin: 0 0 8px; font-size: 1.5rem; }\n"
        "    .meta { margin: 0 0 16px; color: #4b5563; }\n"
        "    .wrap { overflow-x: auto; border: 1px solid #e5e7eb; border-radius: 10px; }\n"
        "    table { width: 100%; border-collapse: collapse; min-width: 720px; }\n"
        "    th, td { padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: left; }\n"
        "    th { position: sticky; top: 0; background: #f8fafc; font-weight: 600; }\n"
        "    tbody tr:nth-child(even) { background: #fcfcfd; }\n"
        "    tbody tr:hover { background: #f5faff; }\n"
        "    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: nowrap; }\n"
        "    .empty { color: #6b7280; font-style: italic; }\n"
        "    a { color: #0b57d0; text-decoration: none; }\n"
        "    a:hover { text-decoration: underline; }\n"
        "    .hint { margin-top: 10px; color: #6b7280; font-size: 0.92rem; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"  <h1>{escape(title)}</h1>\n"
        f"  <p class=\"meta\">Files: {len(rows)} | Total size: {escape(_format_size(total_size))}</p>\n"
        "  <div class=\"wrap\">\n"
        "    <table>\n"
        "      <thead><tr><th>Name</th><th>Size</th><th>Datetime</th><th>Download</th></tr></thead>\n"
        "      <tbody>\n"
        f"        {rows_html}\n"
        "      </tbody>\n"
        "    </table>\n"
        "  </div>\n"
        "  <p class=\"hint\">Name downloads a generated M3U playlist with an absolute HTTP URL for VLC.</p>\n"
        "  <script>\n"
        "    (function () {\n"
        "      function downloadM3U(mediaRel, title) {\n"
        "        var absUrl = new URL(mediaRel, window.location.href).href;\n"
        "        var safeTitle = (title || 'playlist').replace(/[\\\\/]/g, '_');\n"
        "        var m3u = '#EXTM3U\\n#EXTINF:-1,' + safeTitle + '\\n' + absUrl + '\\n';\n"
        "        var blob = new Blob([m3u], { type: 'audio/x-mpegurl' });\n"
        "        var blobUrl = URL.createObjectURL(blob);\n"
        "        var a = document.createElement('a');\n"
        "        a.href = blobUrl;\n"
        "        a.download = safeTitle + '.m3u';\n"
        "        document.body.appendChild(a);\n"
        "        a.click();\n"
        "        a.remove();\n"
        "        setTimeout(function () { URL.revokeObjectURL(blobUrl); }, 1000);\n"
        "      }\n"
        "      var links = document.querySelectorAll('a.m3u-generate[data-media]');\n"
        "      for (var i = 0; i < links.length; i++) {\n"
        "        links[i].addEventListener('click', function (ev) {\n"
        "          ev.preventDefault();\n"
        "          var mediaRel = this.getAttribute('data-media');\n"
        "          var title = this.getAttribute('data-title') || this.textContent || 'playlist';\n"
        "          if (!mediaRel) return;\n"
        "          downloadM3U(mediaRel, title);\n"
        "        });\n"
        "      }\n"
        "    })();\n"
        "  </script>\n"
        "</body>\n"
        "</html>\n"
    )

    index_path = out_dir / "index.html"
    tmp_path = out_dir / "index.html.partial"
    tmp_path.write_text(html, encoding="utf-8")
    tmp_path.replace(index_path)
    return index_path
