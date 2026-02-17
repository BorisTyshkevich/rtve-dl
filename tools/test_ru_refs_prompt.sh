#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  tools/test_ru_refs_prompt.sh [options]

Options:
  --backend <codex|claude>       Backend to test. Default: codex
  --model <name>                 Model name/alias (optional).
                                 Defaults: codex=gpt-5.1-codex-mini, claude=sonnet.
  --input <path.tsv>             Input TSV (id<TAB>spanish_text).
                                 Default: tools/fixtures/ru_refs_446.tsv
  --prompt <path.md>             Prompt template with {{PAYLOAD}} placeholder.
                                 Default: src/rtve_dl/prompts/ru_refs.md
  --workdir <dir>                Work directory. Default: tmp/prompt_tests/ru_refs
  --output <path.tsv>            Output TSV path (optional).
  --no-validate                  Skip strict inline validation.
  -h, --help                     Show this help.

Examples:
  tools/test_ru_refs_prompt.sh --backend codex --model gpt-5.1-codex-mini
  tools/test_ru_refs_prompt.sh --backend claude --model sonnet
  tools/test_ru_refs_prompt.sh --backend codex --prompt tmp/ru_refs.experiment.md
USAGE
}

need_cmd() {
  local c="$1"
  if ! command -v "$c" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $c" >&2
    exit 1
  fi
}

BACKEND="codex"
MODEL=""
INPUT="tools/fixtures/ru_refs_446.tsv"
PROMPT_TEMPLATE="src/rtve_dl/prompts/ru_refs.md"
WORKDIR="tmp/prompt_tests/ru_refs"
OUTPUT=""
VALIDATE="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend) BACKEND="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --input) INPUT="$2"; shift 2 ;;
    --prompt) PROMPT_TEMPLATE="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --no-validate) VALIDATE="0"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$BACKEND" != "codex" && "$BACKEND" != "claude" ]]; then
  echo "ERROR: --backend must be codex or claude" >&2
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "ERROR: input file not found: $INPUT" >&2
  exit 1
fi

if [[ ! -f "$PROMPT_TEMPLATE" ]]; then
  echo "ERROR: prompt file not found: $PROMPT_TEMPLATE" >&2
  exit 1
fi

mkdir -p "$WORKDIR"

if [[ -z "$MODEL" ]]; then
  if [[ "$BACKEND" == "codex" ]]; then
    MODEL="gpt-5.1-codex-mini"
  else
    MODEL="sonnet"
  fi
fi

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$WORKDIR/${BACKEND}.out.tsv"
fi

PROMPT_FILE="$WORKDIR/${BACKEND}.prompt.txt"
RUN_LOG="$WORKDIR/${BACKEND}.run.log"
VALIDATE_LOG="$WORKDIR/${BACKEND}.validate.log"

# Resolve Claude aliases consistent with app defaults.
resolve_claude_model() {
  case "$1" in
    sonnet) printf '%s' "claude-sonnet-4-20250514" ;;
    opus) printf '%s' "claude-opus-4-5-20251101" ;;
    *) printf '%s' "$1" ;;
  esac
}

# Build full prompt by replacing {{PAYLOAD}} with raw TSV input.
python3 - "$PROMPT_TEMPLATE" "$INPUT" "$PROMPT_FILE" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
input_path = Path(sys.argv[2])
prompt_path = Path(sys.argv[3])

template = template_path.read_text(encoding="utf-8")
payload = input_path.read_text(encoding="utf-8")
if "{{PAYLOAD}}" not in template:
    raise SystemExit("ERROR: prompt template does not contain {{PAYLOAD}} placeholder")
prompt = template.replace("{{PAYLOAD}}", payload)
prompt_path.write_text(prompt, encoding="utf-8")
PY

if [[ "$BACKEND" == "codex" ]]; then
  need_cmd codex
  cmd=(codex exec -s read-only --output-last-message "$OUTPUT" -m "$MODEL" -)
  if ! "${cmd[@]}" < "$PROMPT_FILE" > "$RUN_LOG" 2>&1; then
    echo "ERROR: codex run failed. See $RUN_LOG" >&2
    exit 1
  fi
else
  need_cmd claude
  CLAUDE_MODEL="$(resolve_claude_model "$MODEL")"
  if ! claude -p --print --model "$CLAUDE_MODEL" < "$PROMPT_FILE" > "$OUTPUT" 2> "$RUN_LOG"; then
    echo "ERROR: claude run failed. See $RUN_LOG" >&2
    exit 1
  fi
fi

if [[ ! -s "$OUTPUT" ]]; then
  echo "ERROR: empty output file: $OUTPUT" >&2
  exit 1
fi

# Normalize backend output to TSV rows matching input IDs.
# This handles occasional wrapper text from some CLIs (especially Claude).
python3 - "$INPUT" "$OUTPUT" <<'PY'
from pathlib import Path
import sys

input_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])

input_ids: set[str] = set()
for raw in input_path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.rstrip("\r")
    if not line.strip() or "\t" not in line:
        continue
    cue_id = line.split("\t", 1)[0].strip()
    if cue_id:
        input_ids.add(cue_id)

raw_text = output_path.read_text(encoding="utf-8", errors="replace")
kept: list[str] = []
for raw in raw_text.splitlines():
    line = raw.rstrip("\r")
    if not line.strip() or "\t" not in line:
        continue
    cue_id, rest = line.split("\t", 1)
    cue_id = cue_id.strip()
    if cue_id in input_ids:
        kept.append(f"{cue_id}\t{rest.strip()}")

if kept:
    normalized = "\n".join(kept) + "\n"
    if normalized != raw_text:
        backup = output_path.with_suffix(output_path.suffix + ".raw")
        backup.write_text(raw_text, encoding="utf-8")
        output_path.write_text(normalized, encoding="utf-8")
PY

if [[ "$VALIDATE" == "1" ]]; then
  if ! python3 - "$INPUT" "$OUTPUT" > "$VALIDATE_LOG" 2>&1 <<'PY'
from pathlib import Path
import re
import sys

input_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])

def parse_tsv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            raise SystemExit(f"invalid TSV row at {path}:{n}: expected id<TAB>text")
        cue_id = parts[0].strip()
        text = "\t".join(parts[1:]).strip()
        if not cue_id:
            raise SystemExit(f"invalid TSV row at {path}:{n}: empty id")
        out[cue_id] = text
    return out

def looks_like_old_refs_list(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    # Old style examples:
    # "(libertad; justicia por nuestras manos)"
    # "libertad; justicia por nuestras manos"
    if "libertad; justicia por nuestras manos" in t:
        return True
    if ";" in t and "(" in t and ")" in t and len(re.findall(r"[.!?]", t)) == 0:
        return True
    return False

inp = parse_tsv(input_path)
out = parse_tsv(output_path)

if set(inp.keys()) != set(out.keys()):
    missing = sorted(set(inp.keys()) - set(out.keys()))
    extra = sorted(set(out.keys()) - set(inp.keys()))
    raise SystemExit(f"id mismatch: missing={missing[:5]} extra={extra[:5]}")

for cue_id, src in inp.items():
    got = out[cue_id].strip()
    if not got:
        raise SystemExit(f"id {cue_id}: empty output text")
    if looks_like_old_refs_list(got):
        raise SystemExit(f"id {cue_id}: output still looks like old list-style refs: {got}")

    # Must remain a sentence-like line, not pure glossary fragments.
    word_count = len(re.findall(r"\w+", got, flags=re.UNICODE))
    if word_count < 6:
        raise SystemExit(f"id {cue_id}: output too short to be sentence-like: {got}")

    # For cue 446 we require inline RU glossing in brackets with Cyrillic.
    if cue_id == "446":
        if "libertad" not in got and "justicia por nuestras manos" not in got:
            raise SystemExit(f"id 446: key phrase missing from output: {got}")
        if "(" not in got or ")" not in got:
            raise SystemExit(f"id 446: expected inline brackets '(...)': {got}")
        if not re.search(r"\([^\)]*[А-Яа-яЁё][^\)]*\)", got):
            raise SystemExit(f"id 446: expected Cyrillic gloss inside brackets: {got}")

print("OK: strict inline validation passed")
PY
  then
    echo "ERROR: validation failed. See $VALIDATE_LOG" >&2
    cat "$VALIDATE_LOG" >&2
    exit 1
  fi
fi

echo "OK"
echo "Backend:    $BACKEND"
echo "Model:      $MODEL"
echo "Input:      $INPUT"
echo "Prompt:     $PROMPT_TEMPLATE"
echo "BuiltPrompt:$PROMPT_FILE"
echo "Output:     $OUTPUT"
echo "Run log:    $RUN_LOG"
if [[ "$VALIDATE" == "1" ]]; then
  echo "Validate:   $VALIDATE_LOG"
fi
