#!/usr/bin/env bash
# Run the dependency audit with bounded retries for transient registry outages.
# A persistent failure still exits non-zero, so the release gate cannot be
# bypassed when the audit is unavailable or reports a vulnerability.
set -Eeuo pipefail

attempts=3
delay_seconds=10

for ((attempt = 1; attempt <= attempts; attempt++)); do
  echo "::group::npm audit attempt ${attempt}/${attempts}"
  if npm audit --audit-level=high; then
    echo "::endgroup::"
    exit 0
  else
    status=$?
  fi
  echo "::endgroup::"

  if ((attempt < attempts)); then
    echo "npm audit failed with exit ${status}; retrying in ${delay_seconds}s..."
    sleep "${delay_seconds}"
    delay_seconds=$((delay_seconds * 2))
  else
    echo "npm audit failed after ${attempts} attempts; refusing to certify the dependency graph." >&2
    exit "${status}"
  fi
done
