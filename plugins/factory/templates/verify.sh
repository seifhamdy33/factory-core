#!/bin/sh
# .factory/verify, from factory-core plugins/factory/templates/verify.sh (spec: .factory/verify contract).
#   .factory/verify --fast | --full
# It prints one line "factory-verify: <name> PASS|FAIL <seconds>s" per check and exits 0 only if every check
# passes. Keep --fast at or under [verify] fast_budget_s (300 s). Never run checks that read env files or need
# cloud access; CI still runs those.
set -u
mode=${1:---fast}
case "$mode" in --fast|--full) ;; *) echo "usage: .factory/verify --fast|--full" >&2; exit 2 ;; esac
cd "$(git rev-parse --show-toplevel)" || exit 2
failed=0

check() {  # check <name> <command...>
  name=$1; shift
  start=$(date +%s)
  if "$@"; then result=PASS; else result=FAIL; failed=1; fi
  echo "factory-verify: $name $result $(( $(date +%s) - start ))s"
}

changed() {  # files changed since the merge base with origin/main, plus untracked files; deletions excluded
  base=$(git merge-base origin/main HEAD 2>/dev/null || echo HEAD)
  { git diff --name-only --diff-filter=d "$base"; git ls-files --others --exclude-standard; } | sort -u
}

if [ "$mode" = --fast ]; then
  # Replace with this repo's fast checks, scoped to changed files where the tool allows. For example:
  #   ts=$(changed | grep -E '\.tsx?$' | tr '\n' ' '); [ -z "$ts" ] || check eslint npx eslint $ts
  check unit python3 -m unittest discover -s tests -q
else
  # Replace with this repo's full checks.
  check unit python3 -m unittest discover -s tests -q
fi
exit $failed
