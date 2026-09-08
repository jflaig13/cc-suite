# SPDX-License-Identifier: MPL-2.0
"""Company message plane v1 client (PATH C1; reconciler cut from v1)."""

from .client import (
    CompanyMessagePlaneClient,
    CutoverState,
    MessagePlaneError,
    SendResult,
    ClaimResult,
    ConsumeResult,
)

__all__ = [
    "CompanyMessagePlaneClient",
    "CutoverState",
    "MessagePlaneError",
    "SendResult",
    "ClaimResult",
    "ConsumeResult",
]
