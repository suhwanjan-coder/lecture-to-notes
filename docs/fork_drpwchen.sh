#!/usr/bin/env bash
# Fork every drpwchen repo relevant to research / notes / web into your account.
# Requires: gh CLI logged in (`gh auth login`). Run from any directory.
# Skipped on purpose: drpwchen/drpwchen (profile README), fbpost-fork-archive (archived fork),
# loan-invest-sim, fbkit, kimi-webbridge-lockdown (unrelated to our work).
set -euo pipefail
repos=(
  textbook-to-note paper-radar paper-fetch paper-review-and-digest
  vault-search note-supplement openevidence-tools ytscribe asr-benchmark
  exam-practice chart-scrub evernote-rescue codex-pacer
)
# already forked: lecture-to-notes, claude-pacer
for r in "${repos[@]}"; do
  if gh repo view "$(gh api user -q .login)/$r" >/dev/null 2>&1; then
    echo "skip  $r (already in your account)"
  else
    echo "fork  drpwchen/$r"
    gh repo fork "drpwchen/$r" --clone=false --remote=false
  fi
done
