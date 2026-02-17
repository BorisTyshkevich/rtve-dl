# RU Refs Small Subset Expected Behavior

Fixture:
- `tools/fixtures/ru_refs_small.tsv`

## Cue 446
Input:
- `Y es en nombre de la libertad que no tomaremos justicia por nuestras manos.`
Expectation:
- Inline glosses on difficult terms/phrases.
- Example:
  - `... libertad (свобода) ... justicia por nuestras manos (брать правосудие в свои руки) ...`

## Cue 510 (easy line)
Input:
- `No te preocupes, ya vuelvo en cinco minutos.`
Expectation:
- Usually unchanged (A1/A2 vocabulary), no forced glosses.

## Cue 611 (idiom)
Input:
- `Se fue por las ramas y no respondió a lo que le preguntaron.`
Expectation:
- Prefer phrase-level idiom annotation:
  - `por las ramas (в сторону от темы)`
- Keep full Spanish sentence.

## Cue 732 (dense formal register)
Input:
- `En circunstancias excepcionales, la jurisprudencia comparada sugiere aplicar medidas cautelares provisionales para evitar daños irreparables.`
Expectation:
- Keep max 2 inline annotations.
- Prioritize hardest phrases:
  - `jurisprudencia comparada (...)`
  - `medidas cautelares provisionales (...)`
  - or `daños irreparables (...)`
