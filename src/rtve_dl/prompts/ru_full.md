You are translating Spanish subtitles to natural Russian.

Return TSV only, one line per input line, in exactly this format:
id<TAB>text

Rules:
- Keep the same id.
- text must be only Russian translation.
- Preserve literal \n sequences if present in source text.
- No extra columns, no commentary, no markdown, no blank lines.

INPUT TSV:
{{PAYLOAD}}
