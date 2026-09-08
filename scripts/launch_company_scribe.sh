#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail

# This launcher is copied into the reviewed package and is the only runtime
# entrypoint.  The mutable Company workspace is never searched for code,
# settings, hooks, interpreters, or dependencies.
PACKAGE="${MISE_SCRIBE_RELEASE_PACKAGE:?installed package is required}"
PAYLOAD="$PACKAGE/payload"
PYTHON="$PAYLOAD/runtime/python/bin/python3.14"

if [[ "$PACKAGE" != /* ]] \
   || [ ! -f "$PACKAGE/manifest.json" ] \
   || [ ! -x "$PYTHON" ] \
   || [ ! -f "$PAYLOAD/fleet_kernel/company_scribe_runtime.py" ]; then
  echo "REFUSED: incomplete installed Company Scribe runtime package" >&2
  exit 78
fi

export PYTHONHOME="$PAYLOAD/runtime/python"
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONUSERBASE VIRTUAL_ENV
cd "$PAYLOAD"
exec "$PYTHON" -I "$PAYLOAD/fleet_kernel/company_scribe_runtime.py"
