#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  tools/test_complex_terms_titles.sh \
    --input <path/to/file.json|file.jsonl|file.srt|file.vtt> \
    --output <path/to/out.jsonl> \
    [--model gpt-5.3-codex] \
    [--id-key id] \
    [--title-key title] \
    [--limit 0] \
    [--workdir tmp/localpre]

Description:
  POC script for Codex: extract only complex Spanish terms (B1+) and translate them to Russian.
  Supports JSON/JSONL input with title fields, or ES subtitle files (.srt/.vtt).

Output:
  JSONL with schema:
    {"id":"...","terms":[{"es":"...","ru":"..."}]}
USAGE
}

need_cmd() {
  local c="$1"
  if ! command -v "$c" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $c" >&2
    exit 1
  fi
}

INPUT=""
OUTPUT=""
MODEL=""
ID_KEY="id"
TITLE_KEY="title"
LIMIT="0"
WORKDIR="tmp/localpre"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input) INPUT="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --id-key) ID_KEY="$2"; shift 2 ;;
    --title-key) TITLE_KEY="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$INPUT" || -z "$OUTPUT" ]]; then
  echo "ERROR: --input and --output are required" >&2
  usage
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "ERROR: input file not found: $INPUT" >&2
  exit 1
fi

if ! [[ "$LIMIT" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --limit must be a non-negative integer" >&2
  exit 1
fi

need_cmd codex
need_cmd jq
need_cmd awk
need_cmd sed

mkdir -p "$WORKDIR"

base_name="$(basename "$INPUT")"
stem="${base_name%.*}"
PAYLOAD="$WORKDIR/${stem}.complex_terms.payload.jsonl"
PROMPT_FILE="$WORKDIR/${stem}.complex_terms.prompt.txt"
LOG_FILE="${OUTPUT}.log"

normalize_from_json() {
  local src="$1"
  jq -c \
    --arg idk "$ID_KEY" \
    --arg tk "$TITLE_KEY" '
      def rows:
        if type == "array" then .
        elif type == "object" and (.items | type? == "array") then .items
        elif type == "object" then [.] 
        else [] end;

      rows
      | to_entries[]
      | .key as $idx
      | .value as $v
      | ($v[$tk] // $v.title // $v.title_es // $v.name // "") as $t
      | ($v[$idk] // $v.id // $v.key // $v.asset_id // $v.episode_id // ("row_" + (($idx + 1)|tostring))) as $id
      | if (($t|tostring|gsub("^\\s+|\\s+$";"")|length) > 0)
        then {id: ($id|tostring), title: ($t|tostring)}
        else empty
        end
    ' "$src"
}

normalize_from_jsonl() {
  local src="$1"
  jq -Rcs \
    --arg idk "$ID_KEY" \
    --arg tk "$TITLE_KEY" '
      split("\n")
      | map(select(length > 0) | (fromjson? // {}))
      | to_entries[]
      | .key as $idx
      | .value as $v
      | ($v[$tk] // $v.title // $v.title_es // $v.name // "") as $t
      | ($v[$idk] // $v.id // $v.key // $v.asset_id // $v.episode_id // ("row_" + (($idx + 1)|tostring))) as $id
      | if (($t|tostring|gsub("^\\s+|\\s+$";"")|length) > 0)
        then {id: ($id|tostring), title: ($t|tostring)}
        else empty
        end
    ' "$src"
}

normalize_from_subtitles() {
  local src="$1"
  local ext="${src##*.}"

  if [[ "$ext" == "srt" ]]; then
    # Remove sequence numbers and timecode lines; keep text lines.
    awk '
      /^[0-9]+[[:space:]]*$/ { next }
      /^[[:space:]]*[0-9]{2}:[0-9]{2}:[0-9]{2}[,\.][0-9]{3}[[:space:]]*-->/ { next }
      /^[[:space:]]*$/ { next }
      {
        gsub(/<[^>]*>/, "")
        gsub(/^[[:space:]]+|[[:space:]]+$/, "")
        if (length($0) > 0) print $0
      }
    ' "$src"
  else
    # Basic VTT cleanup.
    awk '
      BEGIN { IGNORECASE=1 }
      /^WEBVTT/ { next }
      /^NOTE/ { next }
      /^[[:space:]]*[0-9]{2}:[0-9]{2}:[0-9]{2}[\.][0-9]{3}[[:space:]]*-->/ { next }
      /^[[:space:]]*[0-9]{2}:[0-9]{2}[\.][0-9]{3}[[:space:]]*-->/ { next }
      /^[[:space:]]*$/ { next }
      {
        gsub(/<[^>]*>/, "")
        gsub(/^[[:space:]]+|[[:space:]]+$/, "")
        if (length($0) > 0) print $0
      }
    ' "$src"
  fi | jq -Rnc '[inputs] | to_entries[] | {id:("line_" + ((.key+1)|tostring)), title:.value}'
}

ext_lc="$(printf '%s' "${INPUT##*.}" | tr '[:upper:]' '[:lower:]')"
case "$ext_lc" in
  json)
    normalize_from_json "$INPUT" > "$PAYLOAD"
    ;;
  jsonl)
    normalize_from_jsonl "$INPUT" > "$PAYLOAD"
    ;;
  srt|vtt)
    normalize_from_subtitles "$INPUT" > "$PAYLOAD"
    ;;
  *)
    echo "ERROR: unsupported input extension: .$ext_lc" >&2
    echo "Supported: .json .jsonl .srt .vtt" >&2
    exit 1
    ;;
esac

if [[ "$LIMIT" -gt 0 ]]; then
  head -n "$LIMIT" "$PAYLOAD" > "${PAYLOAD}.tmp"
  mv "${PAYLOAD}.tmp" "$PAYLOAD"
fi

if [[ ! -s "$PAYLOAD" ]]; then
  echo "ERROR: normalized payload is empty: $PAYLOAD" >&2
  exit 1
fi

{
  cat <<'PROMPT'
You extract only complex Spanish terms from episode titles and translate them to Russian.

Return JSONL only.
Exactly one JSON object per input line.
Output line count must exactly match input line count.
No markdown, no explanations, no blank lines.

Complexity policy:
- Keep only B2/C1/C2-level words or phrases.
- Keep idioms, set expressions, figurative phrases if meaning is non-trivial.
- Skip A1/A2/B1 everyday words and trivial function words.

Extraction policy:
- Use ONLY the provided title text.
- Do not use episode context.
- Keep terms in order of first appearance in title.
- Deduplicate within one title (case-insensitive).
- Prefer phrase over split words when phrase is a fixed expression.
- Do not include proper names, places, pure numbers, or punctuation-only tokens,
  unless they are part of an idiom/set phrase.
- If no complex terms found, return empty array.

Translation policy:
- Russian translation must be concise and natural.
- Keep semantic meaning of the title term.
- Lowercase unless proper noun required.
- Do not add comments.

Input JSONL schema:
{"id":"<string>","title":"<spanish_title>"}

Output JSONL schema (keys must be exactly):
{"id":"<string>","terms":[{"es":"<spanish_term_or_phrase>","ru":"<russian_translation>"}]}

INPUT JSONL:
PROMPT
  cat "$PAYLOAD"
} > "$PROMPT_FILE"

cmd=(codex exec -s read-only --output-last-message "$OUTPUT")
if [[ -n "$MODEL" ]]; then
  cmd+=( -m "$MODEL" )
fi
cmd+=( - )

if ! "${cmd[@]}" < "$PROMPT_FILE" > "$LOG_FILE" 2>&1; then
  echo "ERROR: codex exec failed, see log: $LOG_FILE" >&2
  exit 1
fi

if [[ ! -s "$OUTPUT" ]]; then
  echo "ERROR: empty output file: $OUTPUT" >&2
  exit 1
fi

input_lines="$(wc -l < "$PAYLOAD" | tr -d ' ')"
output_lines="$(awk 'NF > 0 {c++} END {print c+0}' "$OUTPUT")"

if [[ "$input_lines" != "$output_lines" ]]; then
  echo "ERROR: line count mismatch input=$input_lines output=$output_lines" >&2
  exit 1
fi

# Strict output schema validation.
awk 'NF > 0' "$OUTPUT" | jq -c -e '
  has("id") and has("terms")
  and (.id | type == "string")
  and (.terms | type == "array")
  and all(.terms[]?; (has("es") and has("ru") and (.es|type=="string") and (.ru|type=="string")))
' >/dev/null

non_empty_terms="$(awk 'NF > 0' "$OUTPUT" | jq -r 'select((.terms|length) > 0) | .id' | wc -l | tr -d ' ')"

echo "OK"
echo "Input:        $INPUT"
echo "Payload:      $PAYLOAD"
echo "Prompt:       $PROMPT_FILE"
echo "Output:       $OUTPUT"
echo "Codex log:    $LOG_FILE"
echo "Input lines:  $input_lines"
echo "Output lines: $output_lines"
echo "With terms:   $non_empty_terms"
