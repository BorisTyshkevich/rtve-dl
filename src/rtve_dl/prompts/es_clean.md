You are editing Spanish subtitle lines produced by ASR.

Task:
- Improve readability with LIGHT normalization only.
- Correct obvious ASR word errors when clear from local context.
- Fix punctuation, capitalization, accents, and spacing.
- Preserve original meaning and tone.
- Do NOT summarize, expand, translate, or rewrite heavily.
- Keep one output line per input id.

Input format (TSV per line):
- col1: id
- col2: current subtitle text
- col3: previous cue text (context)
- col4: next cue text (context)

Output format:
- TSV lines only: id<TAB>cleaned_text
- No prose, no markdown, no code fences.

{{PAYLOAD}}
