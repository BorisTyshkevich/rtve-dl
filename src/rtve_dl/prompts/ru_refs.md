You are creating short Russian learning glosses for Spanish subtitles.

Return TSV only, one line per input line, in exactly this format:
id<TAB>ru_refs

Rules:
- Keep the same id.
- ru_refs must include only difficult B2/C1/C2 words or phrases from the Spanish cue.
- Do not include obvious A1/A2/B1 words.
- Prefer phrase-level glosses for idioms and fixed expressions.
- If no gloss is needed, return an empty ru_refs value.
- Multiple glosses are allowed; separate them with "; ".
- No extra columns, no commentary, no markdown, no blank lines.

INPUT TSV:
{{PAYLOAD}}
