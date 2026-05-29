#!/usr/bin/env bash
# Run latest Claude Code in Docker, pointed at the LiteLLM dev instance.
# Pulls the dev master_key from the in-cluster K8s secret on the
# shared-global cluster (kubectl context: shared-global).
#
# Usage:
#   ./run-claude-dev.sh                # interactive REPL
#   ./run-claude-dev.sh -p "say hi"    # one-shot print mode
#
# Requires: docker, kubectl with shared-global context configured,
#           claude-litellm-dev:latest image (run ./build-claude-dev.sh first).

set -euo pipefail

LITELLM_DEV_URL="https://litellm-api-dev.shared.global.bitmex"
KUBE_CTX="shared-global"
KUBE_NS="gen-ai-dev"
KUBE_SECRET="litellm"

DEV_KEY="$(kubectl --context="${KUBE_CTX}" -n "${KUBE_NS}" \
  get secret "${KUBE_SECRET}" \
  -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)"

if [[ -z "${DEV_KEY}" ]]; then
  echo "failed to fetch LITELLM_MASTER_KEY from K8s secret" >&2
  exit 1
fi

# Persist Claude Code's per-user state across runs without polluting host ~/.claude.
# IMPORTANT: never mount host ~/.claude here — it carries production OAuth tokens.
STATE_DIR="${HOME}/.config/claude-litellm-dev/state"
mkdir -p "${STATE_DIR}"

exec docker run --rm -it \
  -v "${PWD}:/work" \
  -v "${HOME}/Workspace:/root/Workspace" \
  -v "${STATE_DIR}:/root/.claude" \
  -e ANTHROPIC_BASE_URL="${LITELLM_DEV_URL}" \
  -e ANTHROPIC_AUTH_TOKEN="${DEV_KEY}" \
  claude-litellm-dev:latest "$@"
