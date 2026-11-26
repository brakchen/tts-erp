#!/usr/bin/env bash
# Resource admission, independent of database/template identity. Source only from
# test_isolated.sh. util-linux flock releases leases when the last owner exits.
# https://man7.org/linux/man-pages/man1/flock.1.html (util-linux, GPL-2.0-or-later).

test_capacity_acquire() {
  local lock="${TTS_ERP_TEST_CAPACITY_LOCK:-/tmp/tts-erp-test-capacity.lock}"
  local timeout="${TTS_ERP_TEST_CAPACITY_TIMEOUT_S:-7200}"
  local started=$SECONDS class=heavy argument slot targets=0 file
  [[ "$timeout" =~ ^[1-9][0-9]*$ ]] || { echo '[test-queue] invalid timeout' >&2; return 2; }
  # Only explicit, browser-free file/node selections qualify for the small lane.
  # Unknown/domain/selected/full commands keep the conservative exclusive lease.
  if [[ "${1:-}" == fast && $# -gt 1 ]]; then
    class=light
    for argument in "${@:2}"; do
      case "$argument" in
        tests/unit/*.py*|tests/sync_worker/*.py*|tests/fx/*.py*)
          file="${argument%%::*}"
          if [[ "$file" =~ ^tests/(unit|sync_worker|fx)/[a-zA-Z0-9_]+\.py$ && -f "$file" &&
                "$(realpath -e -- "$file")" == "$(pwd -P)/$file" ]]; then
            targets=$((targets + 1))
          else class=heavy; fi ;;
        --tb=short|--tb=line|--tb=long|-q|-v|-vv|--disable-warnings) ;;
        *) class=heavy ;;
      esac
    done
  fi
  if [[ "$targets" -eq 0 || "$targets" -gt 8 ]]; then class=heavy; fi
  exec 6>"${lock}.gate" 7>"$lock"
  echo "[test-queue] waiting class=$class pid=$$ lock=$lock"
  # The gate prevents a stream of new light readers starving a queued full run.
  _test_capacity_wait -x 6 || return
  if [[ "$class" == heavy ]]; then
    _test_capacity_wait -x 7 || return
  else
    _test_capacity_wait -s 7 || return
    while :; do
      for slot in 0 1; do
        exec 8>"${lock}.light-$slot"
        if flock -n -x 8; then break 2; fi
        exec 8>&-
      done
      (( SECONDS - started < timeout )) || { echo '[test-queue] capacity timeout' >&2; return 2; }
      sleep 1
    done
  fi
  flock -u 6
  exec 6>&-
  # Frontend may use the fixed three-worker budget only after exclusive admission.
  export TTS_ERP_TEST_CAPACITY_CLASS="$class"
  printf '[test-timing] capacity_wait=%ds\n' "$((SECONDS - started))"
  echo "[test-queue] admitted class=$class pid=$$"
}

_test_capacity_wait() {
  local remaining
  while :; do
    remaining=$((timeout - SECONDS + started))
    (( remaining > 0 )) || { echo '[test-queue] capacity timeout' >&2; return 2; }
    if flock -w "$((remaining < 20 ? remaining : 20))" "$@"; then return 0; fi
    echo "[test-queue] waiting class=$class elapsed=$((SECONDS - started))s"
  done
}
