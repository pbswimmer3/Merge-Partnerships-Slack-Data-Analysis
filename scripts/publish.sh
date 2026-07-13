#!/bin/bash
set -euo pipefail

# Triggers the daily-partnerships-analysis.yml workflow remotely via `gh`,
# waits for it to finish, then prints the GitHub Pages URL.
# Usage: ./scripts/publish.sh [days]

DAYS="${1:-1}"
WORKFLOW="daily-partnerships-analysis.yml"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found - install from https://cli.github.com" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh CLI not authenticated - run: gh auth login" >&2
  exit 1
fi

# Record the current latest run so we can tell it apart from the one we're
# about to trigger (gh workflow run is async and returns before the new run
# is registered, so "just list the latest run" can pick up a prior run).
BEFORE_ID=$(gh run list --workflow="$WORKFLOW" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || echo "")

gh workflow run "$WORKFLOW" -f days="$DAYS"

RUN_ID=""
for _ in $(seq 1 30); do
  CANDIDATE=$(gh run list --workflow="$WORKFLOW" --limit 1 --json databaseId --jq '.[0].databaseId')
  if [ -n "$CANDIDATE" ] && [ "$CANDIDATE" != "$BEFORE_ID" ]; then
    RUN_ID="$CANDIDATE"
    break
  fi
  sleep 2
done

if [ -z "$RUN_ID" ]; then
  echo "Timed out waiting for the triggered run to be registered." >&2
  exit 1
fi

gh run watch "$RUN_ID" --exit-status

REPO=$(gh repo view --json owner,name --jq '.owner.login + "/" + .name')
OWNER_REPO=$(printf '%s' "$REPO" | tr '[:upper:]' '[:lower:]')
OWNER=$(printf '%s' "$OWNER_REPO" | cut -d/ -f1)
REPO_NAME=$(printf '%s' "$OWNER_REPO" | cut -d/ -f2)

echo "https://${OWNER}.github.io/${REPO_NAME}/"
