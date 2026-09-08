#!/bin/zsh
# SPDX-License-Identifier: MPL-2.0

set -e

ALL_ROLES=(ccto ccpo ccfo ccro ccmo cclo ccgo ccco utility cos ccde)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-${(%):-%x}}")/.." && pwd)"
SCRIPT_DIR="$REPO_ROOT/scripts"
ROLES=""
FORCE=0
DRY_RUN=0
SHOW_HELP=0
TENANT=""
FLEET=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --roles)
            ROLES="$2"
            shift 2
            ;;
        --roles=*)
            ROLES="${1#--roles=}"
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --tenant)
            TENANT="$2"
            shift 2
            ;;
        --tenant=*)
            TENANT="${1#--tenant=}"
            shift
            ;;
        --fleet)
            FLEET="$2"
            shift 2
            ;;
        --fleet=*)
            FLEET="${1#--fleet=}"
            shift
            ;;
        --help|-h)
            SHOW_HELP=1
            shift
            ;;
        *)
            echo "ERR Unknown flag: $1" >&2
            echo "Run: $0 --help" >&2
            exit 1
            ;;
    esac
done

if [[ $SHOW_HELP -eq 1 ]]; then
    cat <<'HELP'
Usage: fleet_spawn.sh [--roles ROLE,ROLE] [--dry-run] [--force]

Launch configured Company roles through the admitted legacy host path.
  --roles LIST  Comma-separated roles; defaults to all supported Company roles.
  --dry-run     Authorize and write launcher command files without opening Terminal.
  --force       Ignore existing-role process detection; authorization still applies.
  --help, -h    Show this help without deployment configuration or state writes.

Supported roles: ccto ccpo ccfo ccro ccmo cclo ccgo ccco utility cos ccde
Scribe and restaurant fleets require their separate admission paths.
See docs/host-runtime.md for required deployment configuration.
HELP
    exit 0
fi

. "$SCRIPT_DIR/portable_host_env.sh" || exit $?
export CC_SUITE_PACKAGE_ROOT="$REPO_ROOT"

LAUNCHER="$REPO_ROOT/scripts/launch_claude.sh"
if [[ ! -x "$LAUNCHER" ]]; then
    echo "ERR Launcher not found or not executable: $LAUNCHER" >&2
    echo "    Is REPO_ROOT correct? ($REPO_ROOT)" >&2
    exit 3
fi

AGM_ROLES=()
if [[ -n "$FLEET" || -n "$TENANT" ]]; then
    echo "REFUSED: restaurant fleet spawning is not part of this Company export" >&2
    exit 78
fi

if [[ -z "$ROLES" ]]; then
    TARGET_ROLES=("${ALL_ROLES[@]}")
else
    TARGET_ROLES=("${(@s:,:)ROLES}")
fi
for role in $TARGET_ROLES; do
    if [[ "$role" == "scribe" ]]; then
        echo "ERR Scribe must launch through the exact admission-aware interactive path" >&2
        exit 78
    fi
done

for role in $TARGET_ROLES; do
    valid=0
    for allowed in $ALL_ROLES $AGM_ROLES; do
        if [[ "$role" == "$allowed" ]]; then
            valid=1
            break
        fi
    done
    if [[ $valid -eq 0 ]]; then
        echo "ERR Unknown role: $role" >&2
        echo "    Allowed: ${ALL_ROLES[*]}" >&2
        exit 2
    fi
done

is_role_running() {
    local role="$1"
    pgrep -f "role-${role}\.mcp\.json" >/dev/null 2>&1
}

LAUNCHERS_DIR="$CC_SUITE_STATE_ROOT/launchers"


write_command_file() {
    local role="$1"
    local target="$LAUNCHERS_DIR/launch_${role}.command"

    "$CC_SUITE_PYTHON" - "$target" "$role" "$LAUNCHER" <<'PY'
import os, shlex, sys
from pathlib import Path
target, role, launcher = sys.argv[1:]
keys = ["CC_SUITE_COMPANY_ID", "CC_SUITE_SCRIBE_DEPLOYMENT_ID",
        "CC_SUITE_WORKSPACE", "CC_SUITE_STATE_ROOT", "CC_SUITE_CHANNEL_SHARED_DIR",
        "CC_SUITE_PYTHON", "CC_SUITE_BUN", "CC_SUITE_HOST_AUTHORIZER",
        "CC_SUITE_CLAUDE", "CC_SUITE_PERMISSION_MODE", "CC_SUITE_PACKAGE_ROOT"]
lines = ["#!/bin/zsh", "set -e"]
for key in keys:
    if key in os.environ:
        lines.append("export " + key + "=" + shlex.quote(os.environ[key]))
lines.append("exec " + shlex.quote(launcher) + " " + shlex.quote(role))
Path(target).write_text("\n".join(lines)+"\n")
PY
    chmod +x "$target"
    echo "$target"
}

echo "Fleet spawn — repo root: $REPO_ROOT"
echo "  Target roles: ${TARGET_ROLES[*]}"
echo "  Force:        $FORCE"
echo "  Dry-run:      $DRY_RUN"
echo ""

SPAWNED=0
SKIPPED=0

for role in $TARGET_ROLES; do
    authorize_host_operation "$role" fleet-spawn || exit $?
    mkdir -p "$LAUNCHERS_DIR"
    if [[ $FORCE -eq 0 ]] && is_role_running "$role"; then
        echo "  [skip] $role — already running (pgrep -f role-${role}.mcp.json matched)"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    cmd_file=$(write_command_file "$role")

    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  [dry]  $role — wrote $cmd_file (Terminal NOT opened)"
    else
        : > "$CC_SUITE_STATE_ROOT/relaunch_${role}.marker" 2>/dev/null || true
        rm -f "$CC_SUITE_STATE_ROOT/role_down_${role}.marker" 2>/dev/null || true
        open -a Terminal "$cmd_file"
        echo "  [open] $role — spawned via $cmd_file"

    fi
    SPAWNED=$((SPAWNED + 1))
done

echo ""
echo "Fleet spawn complete: $SPAWNED spawned, $SKIPPED skipped (already running)"
exit 0
