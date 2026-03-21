#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GIT_BIN="$(command -v /opt/homebrew/bin/git || command -v git || true)"
RG_BIN="$(command -v rg || true)"

fail() {
  echo "repo hygiene check failed: $*" >&2
  exit 1
}

[[ -n "$GIT_BIN" ]] || fail "git is required"
[[ -n "$RG_BIN" ]] || fail "rg is required"

if "$GIT_BIN" ls-files | "$RG_BIN" -n '\.scad$' >/dev/null; then
  fail "tracked .scad file detected"
fi

if "$GIT_BIN" rev-list --objects --all | "$RG_BIN" -n '\.scad($| )' >/dev/null; then
  fail ".scad file found in reachable git history"
fi

for pattern in \
  '^infra/aws/terraform\.tfvars$' \
  '^infra/aws/terraform\.tfstate$' \
  '^infra/aws/terraform\.tfstate\.backup$' \
  '^infra/aws/tfplan' \
  '^benchmarks/' \
  '^\.awscheck/' \
  '^instance/'
do
  if "$GIT_BIN" ls-files | "$RG_BIN" -n "$pattern" >/dev/null; then
    fail "tracked local-only file matches pattern: $pattern"
  fi
done

TRACKED_FILES="$("$GIT_BIN" ls-files | "$RG_BIN" -v '^scripts/check_repo_hygiene\.sh$')"
if [[ -n "$TRACKED_FILES" ]]; then
  printf '%s\n' "$TRACKED_FILES" | tr '\n' '\0' | xargs -0 "$RG_BIN" -n --no-heading \
    -e 'AKIA[0-9A-Z]{16}' \
    -e 'ASIA[0-9A-Z]{16}' \
    -e 'aws_secret_access_key' \
    -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' \
    -e 'ghp_[A-Za-z0-9]{20,}' \
    -e 'github_pat_[A-Za-z0-9_]{20,}' \
    -e 'xox[baprs]-[A-Za-z0-9-]+' \
    -e '192\.168\.[0-9]{1,3}\.[0-9]{1,3}' >/dev/null && fail "tracked secret or private-environment marker detected"
fi

echo "repo hygiene check passed"
