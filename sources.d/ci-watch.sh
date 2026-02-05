#!/bin/bash
# ci-watch — Watch a GitHub Actions run until completion.
#
# Args: <run-id | branch-name>
# Requires: gh CLI (authenticated)
#
# Event Source Protocol:
#   Blocks until the CI run completes.
#   Outputs run result (pass/fail details) to stdout.

set -euo pipefail

command -v gh &>/dev/null || { echo "ERROR: gh CLI not installed" >&2; exit 1; }

ARG="${1:?Usage: ci-watch.sh <run-id | branch-name>}"

if [[ "$ARG" =~ ^[0-9]+$ ]]; then
  gh run watch "$ARG" --exit-status 2>&1 || true
else
  RUN_ID=$(gh run list --branch "$ARG" --limit 1 --json databaseId -q '.[0].databaseId')
  if [ -z "$RUN_ID" ]; then
    echo "No runs found for branch: $ARG"
    exit 1
  fi
  gh run watch "$RUN_ID" --exit-status 2>&1 || true
fi
