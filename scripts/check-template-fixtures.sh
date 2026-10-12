#!/usr/bin/env bash
# Checks that every copy of the prompt-template fixture is byte-identical.
#
#   scripts/check-template-fixtures.sh
#
# The backend, the Python SDK and the dashboard each compile `{{name}}` templates with their own
# code and test it against the same cases. The backend copy is the reference; a copy that differs
# from it fails the check, and so does a missing copy.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
reference="backend/tests/prompts/fixtures/template_cases.json"
copies=(
  "sdks/python/tests/fixtures/template_cases.json"
  "frontend/src/features/prompts/template-cases.json"
)

if [[ ! -f "$root/$reference" ]]; then
  echo "missing reference fixture: $reference" >&2
  exit 1
fi

status=0
for copy in "${copies[@]}"; do
  if [[ ! -f "$root/$copy" ]]; then
    echo "MISSING: $copy" >&2
    status=1
    continue
  fi
  if cmp -s "$root/$reference" "$root/$copy"; then
    echo "ok:   $copy"
  else
    echo "DIFF: $copy differs from $reference" >&2
    status=1
  fi
done
exit "$status"
