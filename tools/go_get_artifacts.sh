#!/usr/bin/env bash
# Resolve freshly pushed v8go artifact modules with `go get`.
#
# The staging job pushes the artifact commit and resolves its new
# pseudo-versions immediately with GOPROXY=direct. Checksum verification against
# GOSUMDB stays on. The checksum database publishes a brand-new pseudo-version
# asynchronously and can answer "404 Not Found" for several minutes. A failed
# attempt is retried, a bounded number of times, only when every diagnostic it
# printed is such a lookup miss. Any other diagnostic (a checksum mismatch, a
# VCS or permission error, ...) and a lookup that is still missing after the
# last attempt fail the step.
set -euo pipefail

attempts="${SUMDB_RETRY_ATTEMPTS:-10}"
delay="${SUMDB_RETRY_DELAY_SECONDS:-60}"
[[ "$attempts" =~ ^[1-9][0-9]*$ ]] || { echo "invalid SUMDB_RETRY_ATTEMPTS: $attempts" >&2; exit 2; }
[[ "$delay" =~ ^[0-9]+$ ]] || { echo "invalid SUMDB_RETRY_DELAY_SECONDS: $delay" >&2; exit 2; }
(( $# > 0 )) || { echo "usage: $0 module@version..." >&2; exit 2; }

progress_re='^go: downloading [^ ]+ [^ ]+$'
lookup_miss_re='^go: [^ ]+: [^ ]+: verifying module: [^ ]+: reading https://[^ ]+/lookup/[^ ]+: 404 Not Found$'

# Succeeds only if the log has at least one checksum-database lookup miss and
# nothing except lookup misses and download progress.
only_lookup_misses() {
  local line saw_miss=false
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ $progress_re ]] && continue
    [[ "$line" =~ $lookup_miss_re ]] || return 1
    saw_miss=true
  done <"$1"
  [[ "$saw_miss" == true ]]
}

log="$(mktemp)"
trap 'rm -f -- "$log"' EXIT

for (( attempt = 1; ; attempt++ )); do
  if GOPROXY=direct go get "$@" 2>"$log"; then
    cat "$log" >&2
    exit 0
  fi
  cat "$log" >&2
  if ! only_lookup_misses "$log"; then
    exit 1
  fi
  if (( attempt >= attempts )); then
    echo "checksum database still has no record of the artifact modules after $attempts attempts" >&2
    exit 1
  fi
  echo "checksum database has not published the artifact modules yet; retrying in ${delay}s (attempt $attempt/$attempts)" >&2
  sleep "$delay"
done
