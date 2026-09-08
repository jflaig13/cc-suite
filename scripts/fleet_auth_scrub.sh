#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Remove ambient API authentication variables from the shell that launches
# a fleet process. Source this file in that shell so the unset operations
# affect the child process; running it in a separate shell is insufficient.
#
# An explicitly configured deployment may allow ambient authentication with:
#   MISE_ALLOW_AMBIENT_ANTHROPIC_AUTH=1 <launcher> ...
# Diagnostics print variable names only, never credential values.
# Shell-portable: no indirect expansion (callers may use bash or zsh).

# F6-SCRUB-BEGIN
if [ "${MISE_ALLOW_AMBIENT_ANTHROPIC_AUTH:-0}" != "1" ]; then
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "[fleet_auth_scrub $(date -u +%Y-%m-%dT%H:%M:%SZ)] TRIPWIRE: rogue auth var ANTHROPIC_API_KEY present in parent env — scrubbed before the fleet spawn (variable NAME only; the value is never printed). Unexpected ambient authentication can select the wrong account: route to the deployment owner." >&2
    unset ANTHROPIC_API_KEY
  fi
  if [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "[fleet_auth_scrub $(date -u +%Y-%m-%dT%H:%M:%SZ)] TRIPWIRE: rogue auth var ANTHROPIC_AUTH_TOKEN present in parent env — scrubbed before the fleet spawn (variable NAME only; the value is never printed). Unexpected ambient authentication can select the wrong account: route to the deployment owner." >&2
    unset ANTHROPIC_AUTH_TOKEN
  fi
  if [ -n "${CLAUDE_API_KEY:-}" ]; then
    echo "[fleet_auth_scrub $(date -u +%Y-%m-%dT%H:%M:%SZ)] TRIPWIRE: rogue auth var CLAUDE_API_KEY present in parent env — scrubbed before the fleet spawn (variable NAME only; the value is never printed). Unexpected ambient authentication can select the wrong account: route to the deployment owner." >&2
    unset CLAUDE_API_KEY
  fi
fi
# F6-SCRUB-END
