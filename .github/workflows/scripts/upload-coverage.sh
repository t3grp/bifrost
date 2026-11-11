#!/usr/bin/env bash
set -euo pipefail

# Run after tests, with CODECOV_TOKEN available only to this step.
# Usage: bash upload-coverage.sh framework|plugins|transports
# The digest is from the official v11.3.2 release asset's GitHub metadata:
# https://github.com/codecov/codecov-cli/releases/tag/v11.3.2
readonly CODECOV_CLI_VERSION="v11.3.2"
readonly CODECOV_CLI_SHA256="8e1bfb71661378530f6ea21e82b414095899e565cbb4cf555c613064be17b973"
readonly CODECOV_CLI_URL="https://github.com/codecov/codecov-cli/releases/download/${CODECOV_CLI_VERSION}/codecovcli_linux"

# Kept sourceable for offline tamper-detection tests. Production always supplies
# the fixed release digest above; no environment variable can replace it.
verify_sha256() {
  local path="$1" expected="$2"
  printf '%s  %s\n' "$expected" "$path" | sha256sum --check --status
}

main() (
  local component="${1:-}" repo_root module report flag uploader_dir uploader
  local -a modules=()
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
  cd "$repo_root"

  case "$component" in
    framework|transports) modules=("$component") ;;
    plugins)
      for report in plugins/*/coverage.txt; do
        [[ -f "$report" ]] || continue
        modules+=("${report%/coverage.txt}")
      done
      ;;
    *)
      echo "Usage: $0 framework|plugins|transports" >&2
      return 2
      ;;
  esac

  if [[ -z "${CODECOV_TOKEN:-}" ]]; then
    echo "CODECOV_TOKEN is not configured; coverage reports remain available locally."
    return 0
  fi
  if (( ${#modules[@]} == 0 )); then
    echo "No plugin coverage reports were produced; nothing to upload."
    return 0
  fi
  for module in "${modules[@]}"; do
    if [[ ! -f "$module/coverage.txt" ]]; then
      echo "::error::Missing coverage report: $module/coverage.txt" >&2
      return 1
    fi
  done
  if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "::error::The pinned Codecov uploader requires a Linux x86_64 runner." >&2
    return 1
  fi

  uploader_dir="$(mktemp -d)"
  trap 'rm -rf "$uploader_dir"' EXIT
  uploader="$uploader_dir/codecovcli"
  curl --fail --silent --show-error --location --retry 2 \
    --connect-timeout 10 --max-time 120 \
    --proto '=https' --tlsv1.2 "$CODECOV_CLI_URL" --output "$uploader"
  if ! verify_sha256 "$uploader" "$CODECOV_CLI_SHA256"; then
    echo "::error::Codecov uploader checksum mismatch; refusing to execute it." >&2
    return 1
  fi
  chmod +x "$uploader"

  for module in "${modules[@]}"; do
    flag="$module"
    [[ "$module" != plugins/* ]] || flag="plugin-${module#plugins/}"
    echo "Uploading $module/coverage.txt with flag $flag"
    # v11.3.2's upload-coverage command accepts --file/--flag/--disable-search
    # and --fail-on-error. Disable discovery so only this report is uploaded.
    (
      cd "$module"
      "$uploader" --disable-telem upload-coverage --token "$CODECOV_TOKEN" \
        --file coverage.txt --flag "$flag" --disable-search --fail-on-error
    )
  done
)

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
