#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
# Source-pinned reusable role routing functions, requiring adopter deployment approval.
# Definitions only; this file never starts a host.

get_model_for_role() {
  case "$1" in
    scribe|ccto|ccpo|cos|ccde|ccro|ccfo|ccmo|cclo|ccgo|ccco|utility) echo "claude-fable-5-1" ;;
    mise-orchestrator)                       echo "claude-fable-5-1" ;;
    mise-worker)                             echo "claude-fable-5-1" ;;
    mise-verifier)                           echo "claude-fable-5-1" ;;
    *)                                       echo "claude-sonnet-5" ;;   # default -> current Sonnet
  esac
}

get_grok_effort_for_role() {
  case "$1" in
    ccde)                                              echo "medium" ;;
    utility)                                           echo "high" ;;
    ccro|ccfo|cclo|ccmo|ccgo|ccco)                     echo "xhigh" ;;
    *)                                                 echo "medium" ;;
  esac
}

resolve_routed_override() {
  local role="$1"
  local build_model="${MISE_BUILD_MODEL:-}"
  local build_effort="${MISE_BUILD_EFFORT:-}"
  local contract="${MISE_ROUTING_CONTRACT:-}"

  if [ -z "$build_model" ] && [ -z "$build_effort" ] && [ -z "$contract" ]; then
    return 0
  fi

  case " ccpo scribe cos " in
    *" $role "*)
      echo "REFUSE-LOUD (Plan-First verification-seat deny-list, amendment A1): role '$role' is a verification seat — routed overrides are refused UNCONDITIONALLY, even on an exact contract match (a plan may never choose its own judge; this is the CMCV model-layer independence invariant's hard floor, not belt-and-suspenders). Falling back to the role default." >&2
      return 1
      ;;
  esac

  if [ -z "$contract" ] || [ ! -f "$contract" ]; then
    echo "REFUSE-LOUD (Plan-First anti-laundering gate): MISE_BUILD_MODEL/MISE_BUILD_EFFORT requested for role '$role' but MISE_ROUTING_CONTRACT is missing or not a real file ('$contract') — falling back to the role default. An env var alone can never re-model the fleet; dispatch honors a real, matching contract or nothing." >&2
    return 1
  fi

  local routing_line
  routing_line="$(grep -m1 '^ROUTING:' "$contract" 2>/dev/null)"
  if [ -z "$routing_line" ]; then
    echo "REFUSE-LOUD (Plan-First anti-laundering gate): contract '$contract' has no ROUTING: line for role '$role' — falling back to the role default." >&2
    return 1
  fi

  local contract_model contract_effort contract_class contract_role
  contract_model="$(echo "$routing_line" | grep -oE 'model=[^ ]+' | cut -d= -f2)"
  contract_effort="$(echo "$routing_line" | grep -oE 'effort=[^ ]+' | cut -d= -f2)"
  contract_class="$(echo "$routing_line" | grep -oE 'class=[^ ]+' | cut -d= -f2)"
  contract_role="$(echo "$routing_line" | grep -oE 'role=[^ ]+' | cut -d= -f2)"
  contract_role="${contract_role:-ccde}"

  if [ "$role" != "$contract_role" ]; then
    echo "REFUSE-LOUD (Plan-First anti-laundering gate, amendment A1): launch role '$role' does NOT match contract '$contract' ROUTING role='$contract_role' — falling back to the role default. A contract routed for one seat can never re-model a different seat." >&2
    return 1
  fi

  if [ -n "$build_model" ] && [ "$build_model" != "$contract_model" ]; then
    echo "REFUSE-LOUD (Plan-First anti-laundering gate): MISE_BUILD_MODEL='$build_model' for role '$role' does NOT match contract '$contract' ROUTING model='$contract_model' — falling back to the role default. A mismatched override is exactly the laundering pattern this gate exists to block." >&2
    return 1
  fi
  if [ -n "$build_effort" ] && [ -n "$contract_effort" ] && [ "$build_effort" != "$contract_effort" ]; then
    echo "REFUSE-LOUD (Plan-First anti-laundering gate): MISE_BUILD_EFFORT='$build_effort' for role '$role' does NOT match contract '$contract' ROUTING effort='$contract_effort' — falling back to the role default." >&2
    return 1
  fi

  echo "routing: contract=$contract class=${contract_class:-?} role=$contract_role model=$contract_model effort=$contract_effort" >&2
  echo "${contract_model}|${contract_effort}"
  return 0
}

get_effort_for_role() {
  case "$1" in
    scribe|ccto|ccpo|cos|ccde|ccro|ccfo|ccmo|cclo|ccgo|ccco|utility) echo "high" ;;
    mise-orchestrator|mise-worker|mise-verifier)                     echo "high" ;;
    *)                                                               echo "high" ;;
  esac
}
