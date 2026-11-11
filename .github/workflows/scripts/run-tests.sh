#!/usr/bin/env bash
set -euo pipefail

# Credential-free preflight only. The protected reusable full suite owns live
# provider and Python/TypeScript SDK tests; governance E2E has its own job.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

if (( $# > 0 )); then
  echo "Usage: $0 (build and local unit/mock tests; no live provider tests)" >&2
  exit 2
fi

source "$SCRIPT_DIR/setup-go-workspace.sh"
echo "Building core..."
(cd core && go build ./...)
echo "Building local MCP fixtures..."
make setup-mcp-tests USE_NODE=:
echo "Running core unit and mock tests..."
bash "$SCRIPT_DIR/test-core-unit.sh"

