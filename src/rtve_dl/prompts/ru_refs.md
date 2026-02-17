You are annotating Spanish subtitle lines with inline Russian learning glosses.

Return TSV only, one line per input line, in exactly this format:
id<TAB>annotated_spanish_text

Rules:
- Keep the same id.
- Output must be the full Spanish sentence from the current cue, with inline glosses only.
- Annotate only difficult B1/B2/C1/C2 terms or phrases.
- Do not annotate obvious A1/A2 words.
- Use only the current cue text (second column). Do not use neighboring cues.
- Insert Russian gloss immediately after the Spanish term/phrase in brackets:
  - `libertad (свобода)`
  - `justicia por nuestras manos (брать правосудие в свои руки)`
- Keep max 2 glosses per cue.
- Preserve original Spanish wording, punctuation, and order.
- Brackets must contain Russian text (Cyrillic), not copied Spanish.
- If no gloss is needed, return the unchanged Spanish sentence.
- No extra columns, no commentary, no markdown, no blank lines.

Good example:
- Input: `446<TAB>Y es en nombre de la libertad que no tomaremos justicia por nuestras manos.`
- Output: `446<TAB>Y es en nombre de la libertad (свобода) que no tomaremos justicia por nuestras manos (брать правосудие в свои руки).`

Bad examples:
- `446<TAB>во имя свободы; взять правосудие в свои руки`  (lost Spanish sentence)
- `446<TAB>(libertad; justicia por nuestras manos)`  (old list format)
- `446<TAB>... libertad (libertad) ...`  (bracket is copied Spanish, not Russian)

INPUT TSV:
{{PAYLOAD}}
