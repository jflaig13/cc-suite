# SPDX-License-Identifier: MPL-2.0
"""Tests never inherit real application identities, DSNs, or auth configuration."""
import os
for key in tuple(os.environ):
    if key in {'CC_SUITE_TEST_PG_BIN', 'CC_SUITE_TEST_STATE_ROOT'}:
        continue
    if key.startswith(("FLEET_KERNEL_", "COMPANY_", "CC_SUITE_", "PG")):
        os.environ.pop(key, None)
os.environ["CC_SUITE_COMPANY_ID"] = "example-company"
os.environ["CC_SUITE_SCRIBE_DEPLOYMENT_ID"] = "Scribe-Example"
