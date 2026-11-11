#!/usr/bin/env bash
set -euo pipefail

# Live governance E2E tests. CI supplies the binary built from this run's source
# and test-only provider credentials after pr-testing approval. Never attach to
# an existing gateway or reuse its database, even if a base URL was supplied.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"
binary="${1:-$REPO_ROOT/tmp/bifrost-http}"
if (( $# > 1 )) || [[ ! -x "$binary" ]]; then
  echo "::error::Provide the executable bifrost-http binary from this test run" >&2
  exit 1
fi
binary="$(cd "$(dirname "$binary")" && pwd)/$(basename "$binary")"
for name in OPENAI_API_KEY ANTHROPIC_API_KEY OPENROUTER_API_KEY; do
  if [[ -z "${!name:-}" ]]; then
    echo "::error::Governance E2E requires the test credential $name" >&2
    exit 1
  fi
done

test_dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/bifrost-governance.XXXXXX")"
app_dir="$test_dir/app"
gateway_pid=""
cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n "$gateway_pid" ]]; then
    kill "$gateway_pid" 2>/dev/null || true
    wait "$gateway_pid" 2>/dev/null || true
  fi
  rm -rf "$test_dir"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$app_dir/data"

python3 - "$REPO_ROOT/tests/governance/config.json" "$app_dir" <<'PY'
import json
from pathlib import Path
import sys

source, destination = Path(sys.argv[1]), Path(sys.argv[2])
config = json.loads(source.read_text())
config["config_store"]["config"]["path"] = str(destination / "data/governance-test.db")
(destination / "config.json").write_text(json.dumps(config))
PY

port="$(python3 - <<'PY'
import socket
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
)"
export BIFROST_TEST_BASE_URL="http://127.0.0.1:$port"
(
  cd "$app_dir"
  exec "$binary" -host 127.0.0.1 -port "$port" -app-dir "$app_dir" -log-level warn
) >"$test_dir/gateway.log" 2>&1 &
gateway_pid=$!

python3 - "$port" "$gateway_pid" <<'PY'
import http.client
import os
import sys
import time

port, pid = map(int, sys.argv[1:])
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        raise SystemExit("Governance test gateway exited before becoming healthy")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/health")
        if connection.getresponse().status == 200:
            break
    except OSError:
        pass
    finally:
        connection.close()
    time.sleep(0.2)
else:
    raise SystemExit("Governance test gateway did not become healthy within 90 seconds")
PY

# A successful probe is not enough if our child exited before tests start.
if ! kill -0 "$gateway_pid" 2>/dev/null; then
  echo "::error::Governance test gateway exited after its health check" >&2
  exit 1
fi

cd "$REPO_ROOT/tests/governance"
GOWORK=off go test -count=1 -timeout 30m ./...
