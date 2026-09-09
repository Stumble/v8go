#!/usr/bin/env bash

set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP must be set}"

# Hosted runners may carry an unrelated Chrome repository whose index is briefly
# inconsistent during a Chrome rollout. Disable it for this ephemeral CI job so
# package installation depends only on repositories the job actually needs.
readonly disabled_sources="$RUNNER_TEMP/disabled-apt-sources"
install -d -m 0700 "$disabled_sources"
for source in \
  /etc/apt/sources.list.d/google-chrome.list \
  /etc/apt/sources.list.d/google-chrome.sources; do
  if [[ -f "$source" ]]; then
    sudo mv "$source" "$disabled_sources/"
  fi
done

for attempt in 1 2 3; do
  if sudo apt-get -o Acquire::Retries=3 update; then
    exit 0
  fi
  if [[ "$attempt" -eq 3 ]]; then
    exit 1
  fi
  sleep "$((attempt * 5))"
done
