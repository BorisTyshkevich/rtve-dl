#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 <source.mp4> <source.srt> <out.flac> <out.srt>" >&2
  exit 1
fi

src_mp4="$1"
src_srt="$2"
out_flac="$3"
out_srt="$4"

mkdir -p "$(dirname "$out_flac")" "$(dirname "$out_srt")"

ffmpeg -hide_banner -nostdin -loglevel error -y \
  -ss 0 -t 300 \
  -i "$src_mp4" \
  -vn -ac 1 -ar 16000 -c:a flac \
  "$out_flac"

awk -v start_ms=0 -v end_ms=300000 '
function to_ms(ts, a) {
  gsub(/\r/, "", ts)
  split(ts, a, /[:,]/)
  return ((a[1] * 60 + a[2]) * 60 + a[3]) * 1000 + a[4]
}
function fmt_ms(ms, hh, mm, ss, mmm) {
  if (ms < 0) ms = 0
  hh = int(ms / 3600000)
  ms -= hh * 3600000
  mm = int(ms / 60000)
  ms -= mm * 60000
  ss = int(ms / 1000)
  ms -= ss * 1000
  mmm = ms
  return sprintf("%02d:%02d:%02d,%03d", hh, mm, ss, mmm)
}
BEGIN {
  RS = ""
  ORS = ""
}
{
  block_count = split($0, lines, /\n/)
  ts_idx = 0
  text = ""
  for (i = 1; i <= block_count; i++) {
    if (lines[i] ~ /-->/) {
      ts_idx = i
      break
    }
  }
  if (!ts_idx) next
  split(lines[ts_idx], parts, / --> /)
  s = to_ms(parts[1])
  e = to_ms(parts[2])
  if (e <= start_ms || s >= end_ms) next
  if (s < start_ms) s = start_ms
  if (e > end_ms) e = end_ms
  for (i = ts_idx + 1; i <= block_count; i++) {
    gsub(/\r/, "", lines[i])
    if (text != "") text = text "\n"
    text = text lines[i]
  }
  if (text == "") next
  idx += 1
  print idx "\n" fmt_ms(s - start_ms) " --> " fmt_ms(e - start_ms) "\n" text "\n\n"
}
' "$src_srt" > "$out_srt"
