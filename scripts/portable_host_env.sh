#!/usr/bin/env bash
# Sourced configuration guard; values are never printed.
_cc_suite_require_identity() {
  local name="$1" value="$2" LC_ALL=C
  case "$value" in
    ""|[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*)
      echo "REFUSED: $name must be explicitly set to an identifier" >&2
      return 78
      ;;
  esac
  if [ "${#value}" -gt 128 ]; then
    echo "REFUSED: $name must be an identifier of at most 128 characters" >&2
    return 78
  fi
}

_cc_suite_require_config() {
  if [ -z "$2" ]; then
    echo "REFUSED: $1 is required" >&2
    return 78
  fi
}

_cc_suite_require_identity CC_SUITE_COMPANY_ID "${CC_SUITE_COMPANY_ID:-}" || return 78
_cc_suite_require_identity CC_SUITE_SCRIBE_DEPLOYMENT_ID "${CC_SUITE_SCRIBE_DEPLOYMENT_ID:-}" || return 78
export CC_SUITE_COMPANY_ID CC_SUITE_SCRIBE_DEPLOYMENT_ID

_cc_suite_require_config CC_SUITE_WORKSPACE "${CC_SUITE_WORKSPACE:-}" || return 78
_cc_suite_require_config CC_SUITE_STATE_ROOT "${CC_SUITE_STATE_ROOT:-}" || return 78
_cc_suite_require_config CC_SUITE_CHANNEL_SHARED_DIR "${CC_SUITE_CHANNEL_SHARED_DIR:-}" || return 78
_cc_suite_require_config CC_SUITE_PYTHON "${CC_SUITE_PYTHON:-}" || return 78
_cc_suite_require_config CC_SUITE_HOST_AUTHORIZER "${CC_SUITE_HOST_AUTHORIZER:-}" || return 78
export CCSUITE_REPO_ROOT="$CC_SUITE_WORKSPACE"
export CHANNEL_SHARED_DIR="$CC_SUITE_CHANNEL_SHARED_DIR"
export SCRIBE_CHANNEL_SHARED_DIR="$CC_SUITE_CHANNEL_SHARED_DIR"
export CCSUITE_STATE_DIR="$CC_SUITE_STATE_ROOT"

authorize_host_operation() {
  "$CC_SUITE_PYTHON" "$SCRIPT_DIR/portable_host.py" "$1" "$2"
}
