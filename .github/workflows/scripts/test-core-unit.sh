#!/usr/bin/env bash
set -euo pipefail

# Core unit and local MCP/mock tests only. Build the MCP fixtures with
# `make setup-mcp-tests` first. Provider suites belong to the protected full suite.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../../../core"

# Capture discovery before consuming it. A process substitution hides a failed
# `go list` after partial output and can turn an incomplete test run green.
package_list=$(go list -f '{{.ImportPath}}{{"\t"}}{{.Dir}}' ./...)
packages=()
shopt -s nullglob
while IFS=$'\t' read -r package directory; do
  [[ -n "$package" ]] || continue
  if [[ ! -d "$directory" ]]; then
    echo "::error::Missing source directory for $package: $directory" >&2
    exit 1
  fi
  if [[ "$package" == */internal/llmtests || "$package" == */internal/llmtests/* ]]; then
    continue
  fi
  test_files=("$directory"/*_test.go)
  (( ${#test_files[@]} > 0 )) || continue
  if grep -Fq '/internal/llmtests' "${test_files[@]}"; then
    echo "Skipping live-provider test package: $package"
    continue
  else
    grep_status=$?
    if [[ "$grep_status" -ne 1 ]]; then
      echo "::error::Could not inspect test imports for $package" >&2
      exit "$grep_status"
    fi
  fi
  packages+=("$package")
done <<< "$package_list"

if (( ${#packages[@]} == 0 )); then
  echo "::error::No core unit-test packages found" >&2
  exit 1
fi
go test -count=1 -timeout 20m "${packages[@]}"
