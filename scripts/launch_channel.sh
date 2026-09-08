#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$SCRIPT_DIR/portable_host_env.sh"
ROLE="${1:?Company role required}"
MODE="${2:?push or relay required}"
authorize_host_operation "$ROLE" "channel-$MODE"
if [[ "$ROLE" = scribe ]]; then
  echo "REFUSED: Company Scribe channel is launched by the admitted package supervisor" >&2
  exit 78
fi
export AGENT_ROLE="$ROLE" RELAY_ROLE="$ROLE"
export PYTHONPATH="$PACKAGE_ROOT"
export CC_SUITE_PACKAGE_ROOT="$PACKAGE_ROOT"
export CC_SUITE_CHANNEL_STATE_DIR="$CC_SUITE_STATE_ROOT/company-$ROLE/channel"
cd "$CC_SUITE_WORKSPACE"
case "$MODE" in
  push)
    : "${CC_SUITE_BUN:?explicit Bun executable required}"
    exec "$CC_SUITE_BUN" --no-install "$PACKAGE_ROOT/channels/agent/channel.bundle.js"
    ;;
  relay) exec "$CC_SUITE_PYTHON" -m mcp_servers.channel_relay ;;
  *) echo "REFUSED: unknown channel mode" >&2; exit 78 ;;
esac
