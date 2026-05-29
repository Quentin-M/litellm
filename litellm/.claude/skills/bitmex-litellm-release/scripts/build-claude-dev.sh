#!/usr/bin/env bash
# Build the claude-litellm-dev container image. Stages a temp build context
# with the BitMEX CA (looked up from common host locations).
#
# Usage:
#   ./build-claude-dev.sh [--version <claude-code-version>]

set -euo pipefail

VERSION="latest"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

# Find BitMEX CA on host. Add paths as needed.
CA_CANDIDATES=(
  "${HOME}/Workspace/frontend/bitmex-ca.crt"
  "${HOME}/Workspace/compliance/bitmex-ca.crt"
  "${HOME}/.bitmex/bitmex-ca.crt"
  "/usr/local/share/ca-certificates/bitmex-ca.crt"
)
CA_PATH=""
for c in "${CA_CANDIDATES[@]}"; do
  [[ -r "$c" ]] && CA_PATH="$c" && break
done
if [[ -z "${CA_PATH}" ]]; then
  echo "BitMEX CA not found in known locations:" >&2
  printf '  %s\n' "${CA_CANDIDATES[@]}" >&2
  echo "Pass it via host: copy to one of the above paths." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

cp "${SCRIPT_DIR}/Dockerfile.claude-dev" "${BUILD_DIR}/Dockerfile"
cp "${CA_PATH}" "${BUILD_DIR}/bitmex-ca.crt"

docker build \
  --build-arg "CLAUDE_CODE_VERSION=${VERSION}" \
  -t claude-litellm-dev:latest \
  "${BUILD_DIR}"

echo
echo "Built claude-litellm-dev:latest (Claude Code ${VERSION})"
echo "Run with: ${SCRIPT_DIR}/run-claude-dev.sh"
