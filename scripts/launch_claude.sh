#!/bin/bash
# SPDX-License-Identifier: MPL-2.0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$SCRIPT_DIR/portable_host_env.sh" || exit $?

source "$SCRIPT_DIR/role_model_map.sh"


get_permission_mode_for_role() {
  : "${CC_SUITE_PERMISSION_MODE:?explicit host permission mode required}"
  echo "$CC_SUITE_PERMISSION_MODE"
}

get_port_for_role() {
  case "$1" in
    scribe)  echo 8789 ;;
    ccto)    echo 8790 ;;
    ccpo)    echo 8791 ;;
    utility) echo 8792 ;;
    ccro)    echo 8793 ;;
    ccfo)    echo 8794 ;;
    ccmo)    echo 8795 ;;
    cclo)    echo 8796 ;;
    ccgo)    echo 8797 ;;
    ccco)    echo 8798 ;;
    cos)     echo 8799 ;;
    ccde)    echo 8800 ;;
    *)       echo "" ;;
  esac
}

if [ $# -lt 1 ]; then
  echo "Usage: $(basename "$0") <role> [additional-claude-args...]"
  echo ""
  echo "Roles: scribe ccto ccpo utility ccro ccfo ccmo cclo ccgo ccco cos ccde"
  echo ""
  echo "Reference: docs/host-runtime.md"
  exit 1
fi

ROLE="$1"
shift
EXTRA_ARGS=("$@")
case "$ROLE" in
  scribe|ccto|ccpo|utility|ccro|ccfo|ccmo|cclo|ccgo|ccco|cos|ccde) ;;
  *) echo "REFUSED: only Company roles are exported" >&2; exit 78 ;;
esac
authorize_host_operation "$ROLE" launch-claude || exit $?
: "${CC_SUITE_CLAUDE:?explicit Claude executable required}"
: "${CC_SUITE_PERMISSION_MODE:?explicit host permission mode required}"
export CC_SUITE_PACKAGE_ROOT="$REPO_ROOT"


if [ "$ROLE" = "scribe" ]; then
  echo "REFUSED: writable Company Scribe must launch through the installed exact package supervisor launch-runtime path" >&2
  exit 78
fi

PORT=$(get_port_for_role "$ROLE")
if [ -z "$PORT" ]; then
  echo "ERR Unknown role: $ROLE" >&2
  echo "Valid roles: scribe ccto ccpo utility ccro ccfo ccmo cclo ccgo ccco cos ccde" >&2
  exit 1
fi

GATE_PY="$CC_SUITE_PYTHON"
[ -x "$GATE_PY" ] || { echo "REFUSED: configured Python is not executable" >&2; exit 78; }
GATE_RC=0
MISE_HOST_GATE_SURFACE="launch_claude.sh" "$GATE_PY" \
  "$REPO_ROOT/scripts/company_role_host_gate.py" --role "$ROLE" --host claude \
  || GATE_RC=$?
[ "$GATE_RC" -eq 0 ] || exit "$GATE_RC"

if [ "$ROLE" != "utility" ]; then
  LINEAGE_RC=0
  "$CC_SUITE_PYTHON" \
    "$REPO_ROOT/scripts/company_role_kernel_lineage_probe.py" --role "$ROLE" \
    || LINEAGE_RC=$?
  [ "$LINEAGE_RC" -eq 0 ] || exit "$LINEAGE_RC"
fi

export MISE_ROLE="$ROLE"
export AGENT_ROLE="$ROLE"
export CCSUITE_ROLE="$ROLE"

if [ "${AUTONOMY_COGNITION:-0}" = "1" ]; then
  LAUNCH_HEADLESS=1
fi

if [ "${LAUNCH_HEADLESS:-0}" = "1" ]; then
  if [ -z "${HEADLESS_PROMPT:-}" ] || [ ! -f "${HEADLESS_PROMPT:-}" ]; then
    echo "ERR LAUNCH_HEADLESS=1 but HEADLESS_PROMPT is unset or not a file: '${HEADLESS_PROMPT:-}'" >&2
    exit 78
  fi
fi

. "$REPO_ROOT/scripts/fleet_auth_scrub.sh"

ROLE_MODEL=$(get_model_for_role "$ROLE")

ROLE_EFFORT=$(get_effort_for_role "$ROLE")

ROUTED_OVERRIDE="$(resolve_routed_override "$ROLE")"
if [ -n "$ROUTED_OVERRIDE" ]; then
  ROLE_MODEL="${ROUTED_OVERRIDE%%|*}"
  ROLE_EFFORT="${ROUTED_OVERRIDE##*|}"
fi

FLEET_MODEL="${MODEL:-$ROLE_MODEL}"
FLEET_EFFORT="${EFFORT:-$ROLE_EFFORT}"

if [ -z "$FLEET_MODEL" ]; then
  echo "ERR $ROLE resolved no model — scripts/role_model_map.sh get_model_for_role is the single source; fix the map." >&2
  exit 78
fi
export CCSUITE_HOST_FAMILY="fable"

ROLE_PERMISSION_MODE=$(get_permission_mode_for_role "$ROLE")
PERMISSION_MODE="${PERMISSION_MODE:-$ROLE_PERMISSION_MODE}"

CHANNEL_NAME="${ROLE}-channel-push"

echo "=== launch_claude.sh ==="
echo "Role:             $ROLE"
echo "Channel:          $CHANNEL_NAME"
echo "Port:             $PORT"
echo "Model:            $FLEET_MODEL"
echo "Effort:           $FLEET_EFFORT"
echo "Permission mode:  $PERMISSION_MODE"
echo ""

printf '\033]0;mise-%s\007' "$ROLE"

if [ "${LAUNCH_HEADLESS:-0}" != "1" ] && [ "${#EXTRA_ARGS[@]}" -eq 0 ]; then
  INIT_CMD_FILE="$CC_SUITE_WORKSPACE/.claude/commands/init-${ROLE}.md"
  if [ ! -f "$INIT_CMD_FILE" ]; then
    echo "ERR Init command not found: $INIT_CMD_FILE" >&2
    echo "    /init-${ROLE} would dead-end with no init file to execute — refusing before opening a window." >&2
    exit 1
  fi
  EXTRA_ARGS=("/init-${ROLE}")
fi

export MISE_ROLE="$ROLE"
mkdir -p "$CC_SUITE_CHANNEL_SHARED_DIR"
LAUNCH_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "${AUTONOMY_COGNITION:-0}" != "1" ]; then
  printf '{"role":"%s","started_at":"%s"}\n' "$ROLE" "$LAUNCH_TS" \
    > "$CC_SUITE_CHANNEL_SHARED_DIR/init_pending_${ROLE}.json"
fi
mkdir -p "$CC_SUITE_STATE_ROOT/mcp"
FLEET_HOOK_RESOLVED="$CC_SUITE_STATE_ROOT/mcp/.fleet-hook-${ROLE}.json"
sed "s|@ROLE@|$ROLE|g" \
  "$REPO_ROOT/channels/configs/fleet-hook.settings.json" > "$FLEET_HOOK_RESOLVED" 2>/dev/null || true

CHANNELLESS=0
if [ "${AUTONOMY_COGNITION:-0}" = "1" ]; then
  CHANNELLESS=1
  echo "AUTONOMY_COGNITION=1 — CHANNEL-LESS headless cognition (skipping port-$PORT preflight + channel server; will not touch the live Scribe on 8789)"
fi

EXISTING_PID=""
if [ "$CHANNELLESS" != "1" ]; then
  EXISTING_PID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1)
fi
if [ -n "$EXISTING_PID" ]; then
  echo "REFUSED: role channel port $PORT already has a listener; no process was stopped" >&2
  exit 78
elif [ "$CHANNELLESS" = "1" ]; then
  : # channel-less cognition — no port to preflight (binds nothing)
else
  echo "OK Port $PORT is free"
fi

ROLE_CONFIG="$REPO_ROOT/channels/configs/role-${ROLE}.mcp.json"
if [ ! -f "$ROLE_CONFIG" ]; then
  echo "ERR Role config not found: $ROLE_CONFIG" >&2
  echo "    Expected Company role configs in channels/configs/" >&2
  exit 1
fi

ROLE_EXTRAS=()
[ "${CC_SUITE_ENABLE_CHROME:-0}" != "1" ] || ROLE_EXTRAS=("--chrome")

if [ "${LAUNCH_HEADLESS:-0}" = "1" ]; then
  ROLE_EXTRAS=()  # no --chrome in a headless launchctl context
  HEADLESS_BUDGET_USD="${AUTONOMY_RUN_BUDGET_USD:?explicit headless budget required}"
  cd "$CC_SUITE_WORKSPACE" || exit 1
  if [ "$CHANNELLESS" = "1" ]; then
    echo ""
    echo "Launching (headless, CHANNEL-LESS): claude --model $FLEET_MODEL -p <$HEADLESS_PROMPT> --max-budget-usd $HEADLESS_BUDGET_USD ${EXTRA_ARGS[*]} (no channel server, no port bind — co-exists with the live Scribe on 8789)"
    echo ""
    exec "$CC_SUITE_CLAUDE" --model "$FLEET_MODEL" --effort "$FLEET_EFFORT" --permission-mode "$PERMISSION_MODE" \
      -p "$(cat "$HEADLESS_PROMPT")" --max-budget-usd "$HEADLESS_BUDGET_USD" \
      "${EXTRA_ARGS[@]}"
  else
    echo ""
    echo "Launching (headless): claude --model $FLEET_MODEL -p <$HEADLESS_PROMPT> --max-budget-usd $HEADLESS_BUDGET_USD ${EXTRA_ARGS[*]} --mcp-config $ROLE_CONFIG --dangerously-load-development-channels server:$CHANNEL_NAME"
    echo ""
    exec "$CC_SUITE_CLAUDE" --model "$FLEET_MODEL" --effort "$FLEET_EFFORT" --permission-mode "$PERMISSION_MODE" \
      -p "$(cat "$HEADLESS_PROMPT")" --max-budget-usd "$HEADLESS_BUDGET_USD" \
      "${EXTRA_ARGS[@]}" --mcp-config "$ROLE_CONFIG" \
      --dangerously-load-development-channels "server:$CHANNEL_NAME"
  fi
fi

echo ""
echo "Launching: claude --model $FLEET_MODEL --effort $FLEET_EFFORT --permission-mode $PERMISSION_MODE ${ROLE_EXTRAS[*]} ${EXTRA_ARGS[*]} --mcp-config $ROLE_CONFIG --dangerously-load-development-channels server:$CHANNEL_NAME"
echo ""

cd "$CC_SUITE_WORKSPACE" || exit 1



exec "$CC_SUITE_CLAUDE" --model "$FLEET_MODEL" --effort "$FLEET_EFFORT" --permission-mode "$PERMISSION_MODE" "${ROLE_EXTRAS[@]}" "${EXTRA_ARGS[@]}" --settings "$FLEET_HOOK_RESOLVED" --mcp-config "$ROLE_CONFIG" --dangerously-load-development-channels "server:$CHANNEL_NAME"
