#!/usr/bin/env bash
set -e
set -o pipefail

now_ts() {
  date '+%Y-%m-%d %H:%M:%S'
}

trim() {
  # shellcheck disable=SC2001
  echo "$1" | sed 's/^ *//; s/ *$//'
}

get_ppid() {
  local pid="$1"
  local out
  out="$(ps -p "$pid" -o ppid= 2>/dev/null || true)"
  trim "$out"
}

get_cmd() {
  local pid="$1"
  ps -p "$pid" -o command= 2>/dev/null || true
}

contains_in_list() {
  local needle="$1"
  shift
  local x
  for x in "$@"; do
    if [ "$x" = "$needle" ]; then
      return 0
    fi
  done
  return 1
}

classify_type() {
  local cmd="$1"
  if echo "$cmd" | grep -q "codex exec"; then
    echo "CODEX"
  elif echo "$cmd" | grep -q "ffmpeg -hide_banner -nostdin"; then
    echo "FFMPEG"
  elif echo "$cmd" | grep -q "curl --location --fail"; then
    echo "CURL"
  elif echo "$cmd" | grep -q "rtve_dl download"; then
    echo "RTVE"
  else
    echo "OTHER"
  fi
}

# 1) Find candidate parent processes for parallel runs.
candidates=()
while IFS= read -r pid; do
  [ -n "$pid" ] || continue
  candidates+=("$pid")
done < <(pgrep -f "rtve_dl download .*--parallel" || true)

if [ "${#candidates[@]}" -eq 0 ]; then
  echo "[$(now_ts)] No active rtve_dl --parallel runs found."
  exit 0
fi

# 2) Keep top-level candidates (exclude those whose parent is also a candidate).
roots=()
for pid in "${candidates[@]}"; do
  ppid="$(get_ppid "$pid")"
  if contains_in_list "$ppid" "${candidates[@]}"; then
    continue
  fi
  roots+=("$pid")
done

# Fallback: if filtering removed everything, use raw candidates.
if [ "${#roots[@]}" -eq 0 ]; then
  roots=("${candidates[@]}")
fi

echo "[$(now_ts)] Active rtve_dl --parallel runs: ${#roots[@]}"
echo

idx=1
for root in "${roots[@]}"; do
  root_cmd="$(get_cmd "$root")"
  echo "=== Run #$idx (root PID $root) ==="
  echo "$root_cmd"

  # 3) Collect descendants recursively.
  all_pids=("$root")
  queue=("$root")
  while [ "${#queue[@]}" -gt 0 ]; do
    current="${queue[0]}"
    queue=("${queue[@]:1}")
    while IFS= read -r child; do
      [ -n "$child" ] || continue
      if contains_in_list "$child" "${all_pids[@]}"; then
        continue
      fi
      all_pids+=("$child")
      queue+=("$child")
    done < <(pgrep -P "$current" || true)
  done

  # 4) Print details and type counts.
  count_rtve=0
  count_curl=0
  count_ffmpeg=0
  count_codex=0
  count_other=0

  printf "%6s %6s %10s %8s %s\n" "PID" "PPID" "ELAPSED" "TYPE" "COMMAND"
  for pid in "${all_pids[@]}"; do
    line="$(ps -p "$pid" -o pid=,ppid=,etime=,command= 2>/dev/null || true)"
    [ -n "$line" ] || continue

    # Parse fixed columns: pid, ppid, etime, command...
    pid_col="$(echo "$line" | awk '{print $1}')"
    ppid_col="$(echo "$line" | awk '{print $2}')"
    etime_col="$(echo "$line" | awk '{print $3}')"
    cmd_col="$(echo "$line" | cut -d' ' -f4-)"
    cmd_col="$(trim "$cmd_col")"
    typ="$(classify_type "$cmd_col")"

    case "$typ" in
      RTVE) count_rtve=$((count_rtve + 1)) ;;
      CURL) count_curl=$((count_curl + 1)) ;;
      FFMPEG) count_ffmpeg=$((count_ffmpeg + 1)) ;;
      CODEX) count_codex=$((count_codex + 1)) ;;
      *) count_other=$((count_other + 1)) ;;
    esac

    printf "%6s %6s %10s %8s %s\n" "$pid_col" "$ppid_col" "$etime_col" "$typ" "$cmd_col"
  done

  echo "--- Summary: RTVE=$count_rtve CURL=$count_curl FFMPEG=$count_ffmpeg CODEX=$count_codex OTHER=$count_other"
  echo
  idx=$((idx + 1))
done
