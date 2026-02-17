You are translating Spanish subtitles to natural Russian.

Return TSV only, one line per input line, in exactly this format:
id<TAB>text

Rules:
- Keep the same id.
- text must be only Russian translation.
- Preserve literal \n sequences if present in source text.
- Translate foreign/common loan words by meaning into natural Russian; do not transliterate them unless they are proper names/brands.
  - Example: "soirée" -> "светский вечер" (or "вечер"), not "суаре/соаре".
  - Example: "tourneé" -> "гастроли", not "турне" when context clearly means theater tour.
- No extra columns, no commentary, no markdown, no blank lines.

INPUT TSV:
{{PAYLOAD}}
