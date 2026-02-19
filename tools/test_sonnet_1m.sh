#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  tools/test_sonnet_1m.sh [options]

Test script for translating an entire subtitle file using Claude Sonnet's 1M
context window without chunking (Spanish -> Russian full translation).

Options:
  --srt <path>           Input SRT file.
  --vtt <path>           Input VTT file.
  --url <url>            Download VTT from URL first.
  --prompt <path.md>     Prompt template with {{PAYLOAD}} placeholder.
                         Default: src/rtve_dl/prompts/ru_full.md
  --model <name>         Claude model to use.
                         Default: sonnet
  --workdir <dir>        Work directory. Default: tmp/sonnet_1m_test
  -h, --help             Show this help.

Examples:
  tools/test_sonnet_1m.sh --srt tmp/cuentameT8/srt/S08E01_foo.spa.srt
  tools/test_sonnet_1m.sh --vtt tmp/cuentameT8/vtt/12345.vtt
  tools/test_sonnet_1m.sh --url https://www.rtve.es/resources/vtt/7/7/1770238852877.vtt
  tools/test_sonnet_1m.sh --model opus
USAGE
}

need_cmd() {
  local c="$1"
  if ! command -v "$c" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $c" >&2
    exit 1
  fi
}

SRT=""
VTT=""
URL=""
PROMPT_TEMPLATE="src/rtve_dl/prompts/ru_full.md"
MODEL="sonnet"
WORKDIR="tmp/sonnet_1m_test"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --srt) SRT="$2"; shift 2 ;;
    --vtt) VTT="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    --prompt) PROMPT_TEMPLATE="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

# Ensure workdir exists early (needed for URL download)
mkdir -p "$WORKDIR"

# Handle URL download
if [[ -n "$URL" ]]; then
  need_cmd curl
  VTT="$WORKDIR/downloaded.vtt"
  echo "Downloading VTT from $URL..."
  curl -sL "$URL" -o "$VTT"
  echo "Downloaded to $VTT"
fi

# Determine input type
INPUT_FILE=""
INPUT_TYPE=""
if [[ -n "$SRT" ]]; then
  INPUT_FILE="$SRT"
  INPUT_TYPE="srt"
elif [[ -n "$VTT" ]]; then
  INPUT_FILE="$VTT"
  INPUT_TYPE="vtt"
else
  echo "ERROR: Must specify --srt, --vtt, or --url" >&2
  usage
  exit 1
fi

if [[ ! -f "$INPUT_FILE" ]]; then
  echo "ERROR: Input file not found: $INPUT_FILE" >&2
  exit 1
fi

if [[ ! -f "$PROMPT_TEMPLATE" ]]; then
  echo "ERROR: prompt template not found: $PROMPT_TEMPLATE" >&2
  exit 1
fi

need_cmd claude
need_cmd python3

INPUT_TSV="$WORKDIR/input.tsv"
PROMPT_FILE="$WORKDIR/prompt.txt"
OUTPUT_TSV="$WORKDIR/output.tsv"
RUN_LOG="$WORKDIR/run.log"

echo "Parsing $INPUT_TYPE -> TSV..."

# Parse SRT or VTT to TSV using Python
python3 - "$INPUT_FILE" "$INPUT_TSV" "$INPUT_TYPE" <<'PY'
import sys
import re
from pathlib import Path

input_path = Path(sys.argv[1])
tsv_path = Path(sys.argv[2])
input_type = sys.argv[3]

text = input_path.read_text(encoding="utf-8")
cues: list[tuple[str, str]] = []

if input_type == "vtt":
    # Parse VTT format
    # Skip WEBVTT header and metadata
    lines = text.splitlines()
    i = 0
    cue_num = 0
    while i < len(lines):
        line = lines[i].strip()
        # Skip empty lines and header
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            i += 1
            continue
        # Look for timestamp line (00:00:00.000 --> 00:00:00.000)
        if "-->" in line:
            i += 1
            cue_num += 1
            # Collect text lines until blank line
            text_lines: list[str] = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].rstrip())
                i += 1
            if text_lines:
                cue_text = "\n".join(text_lines)
                # Strip VTT tags like <c> </c>
                cue_text = re.sub(r"<[^>]+>", "", cue_text)
                cue_text = cue_text.replace("\\", "\\\\")
                cue_text = cue_text.replace("\t", "\\t")
                cue_text = cue_text.replace("\n", "\\n")
                cues.append((str(cue_num), cue_text))
        else:
            i += 1
else:
    # Parse SRT format
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if not line.isdigit():
            i += 1
            continue
        cue_id = line
        i += 1
        if i < len(lines) and "-->" in lines[i]:
            i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].rstrip())
            i += 1
        if text_lines:
            cue_text = "\n".join(text_lines)
            cue_text = cue_text.replace("\\", "\\\\")
            cue_text = cue_text.replace("\t", "\\t")
            cue_text = cue_text.replace("\n", "\\n")
            cues.append((cue_id, cue_text))

# Write TSV
with open(tsv_path, "w", encoding="utf-8") as f:
    for cue_id, cue_text in cues:
        f.write(f"{cue_id}\t{cue_text}\n")

print(f"Parsed {len(cues)} cues")
PY

INPUT_CUES=$(wc -l < "$INPUT_TSV" | tr -d ' ')
echo "Input cues: $INPUT_CUES"

# Build prompt by replacing {{PAYLOAD}} with TSV content
echo "Building prompt..."
python3 - "$PROMPT_TEMPLATE" "$INPUT_TSV" "$PROMPT_FILE" <<'PY'
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

# Report size
kb = len(prompt.encode("utf-8")) / 1024
print(f"Prompt size: {kb:.1f} KB")
PY

# Run Claude
echo "Running Claude ($MODEL)..."
echo "This may take a while for ~$INPUT_CUES cues..."
START_TIME=$(date +%s)

if ! claude -p --print --model "$MODEL" < "$PROMPT_FILE" > "$OUTPUT_TSV" 2> "$RUN_LOG"; then
  echo "ERROR: claude run failed. See $RUN_LOG" >&2
  cat "$RUN_LOG" >&2
  exit 1
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

# Normalize output - extract valid TSV lines matching input IDs
python3 - "$INPUT_TSV" "$OUTPUT_TSV" <<'PY'
from pathlib import Path
import sys

input_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])

# Collect input IDs
input_ids: set[str] = set()
for line in input_path.read_text(encoding="utf-8").splitlines():
    if "\t" in line:
        cue_id = line.split("\t", 1)[0].strip()
        if cue_id:
            input_ids.add(cue_id)

# Filter output to valid TSV lines
raw_text = output_path.read_text(encoding="utf-8")
kept: list[str] = []
for line in raw_text.splitlines():
    line = line.strip()
    if not line or "\t" not in line:
        continue
    cue_id = line.split("\t", 1)[0].strip()
    if cue_id in input_ids:
        kept.append(line)

if kept:
    normalized = "\n".join(kept) + "\n"
    if normalized != raw_text:
        backup = output_path.with_suffix(".tsv.raw")
        backup.write_text(raw_text, encoding="utf-8")
        output_path.write_text(normalized, encoding="utf-8")
        print(f"Normalized output ({len(kept)} lines, raw saved to {backup.name})")
    else:
        print(f"Output already normalized ({len(kept)} lines)")
PY

OUTPUT_CUES=$(wc -l < "$OUTPUT_TSV" | tr -d ' ')

echo ""
echo "=== Results ==="
echo "Input file:   $INPUT_FILE"
echo "Model:        $MODEL"
echo "Input cues:   $INPUT_CUES"
echo "Output cues:  $OUTPUT_CUES"
echo "Elapsed:      ${ELAPSED}s"
echo ""
echo "Prompt:       $PROMPT_FILE"
echo "Output TSV:   $OUTPUT_TSV"
echo "Run log:      $RUN_LOG"

# Verify cue counts match
if [[ "$INPUT_CUES" -ne "$OUTPUT_CUES" ]]; then
  DIFF=$((INPUT_CUES - OUTPUT_CUES))
  echo ""
  echo "WARNING: Output has $DIFF fewer cues than input"

  # Show missing IDs
  python3 - "$INPUT_TSV" "$OUTPUT_TSV" <<'PY'
from pathlib import Path
import sys

input_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])

input_ids = set()
for line in input_path.read_text(encoding="utf-8").splitlines():
    if "\t" in line:
        input_ids.add(line.split("\t", 1)[0].strip())

output_ids = set()
for line in output_path.read_text(encoding="utf-8").splitlines():
    if "\t" in line:
        output_ids.add(line.split("\t", 1)[0].strip())

missing = sorted(input_ids - output_ids, key=lambda x: int(x) if x.isdigit() else 0)
if missing:
    preview = missing[:10]
    print(f"Missing IDs: {preview}" + ("..." if len(missing) > 10 else ""))
PY
fi

# Show sample output
echo ""
echo "=== Sample output (first 5 cues) ==="
head -5 "$OUTPUT_TSV"
