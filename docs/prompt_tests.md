# Prompt Tests

Manual prompt tests for RU refs are available via:
- `tools/test_ru_refs_prompt.sh`

Default fixture:
- `tools/fixtures/ru_refs_446.tsv`
- Small subset fixture:
  - `tools/fixtures/ru_refs_small.tsv`

## Quick Run

Codex:
```bash
tools/test_ru_refs_prompt.sh --backend codex --model gpt-5.1-codex-mini
```

Claude:
```bash
tools/test_ru_refs_prompt.sh --backend claude --model sonnet
```

Run on small subset:
```bash
tools/test_ru_refs_prompt.sh --backend codex --input tools/fixtures/ru_refs_small.tsv
tools/test_ru_refs_prompt.sh --backend claude --input tools/fixtures/ru_refs_small.tsv
```

## Prompt Experiment

Copy and edit prompt:
```bash
cp src/rtve_dl/prompts/ru_refs.md tmp/ru_refs.experiment.md
# edit tmp/ru_refs.experiment.md
```

Run both backends with experimental prompt:
```bash
tools/test_ru_refs_prompt.sh --backend codex --prompt tmp/ru_refs.experiment.md
tools/test_ru_refs_prompt.sh --backend claude --prompt tmp/ru_refs.experiment.md
```

Compare outputs:
```bash
diff -u tmp/prompt_tests/ru_refs/codex.out.tsv tmp/prompt_tests/ru_refs/claude.out.tsv
```

## Validation Rules (default)

The script validates:
- TSV shape (`id<TAB>text`)
- ID parity with input
- Sentence-like output (not glossary-only fragments)
- Rejection of old list style such as `(libertad; justicia por nuestras manos)`
- For cue `446`: inline brackets with Cyrillic gloss text.

Disable validation if you want raw output only:
```bash
tools/test_ru_refs_prompt.sh --backend codex --no-validate
```
