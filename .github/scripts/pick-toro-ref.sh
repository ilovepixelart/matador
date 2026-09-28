#!/bin/sh
# Which toro ref CI checks out as matador's sibling dependency (see pr-check.yaml).
# uv.lock records the sibling's version and `uv sync --locked` rejects any other, so
# without a same-named toro branch the build uses the release the lock was made
# against, not toro main: main moves to the next version before matador relocks.
set -eu
: "${DEFAULT_BRANCH:?the workflow passes the default branch of this repo}"
# Pairing is for feature branches: toro has a `main` too, and toro's main moves to the
# next version before matador relocks.
if [ "$BRANCH" != "$DEFAULT_BRANCH" ] && gh api "repos/$OWNER/toro/branches/$BRANCH" --silent 2>/dev/null; then
  echo "ref=$BRANCH" >> "$GITHUB_OUTPUT"
  exit 0
fi
locked=$(awk '/^name = "toro-queue"$/ { found = 1; next } found && /^version = / { gsub(/"/, "", $3); print $3; exit }' uv.lock)
if [ -n "$locked" ] && gh api "repos/$OWNER/toro/git/ref/tags/v$locked" --silent 2>/dev/null; then
  echo "ref=v$locked" >> "$GITHUB_OUTPUT"
else
  echo "ref=main" >> "$GITHUB_OUTPUT"
fi
