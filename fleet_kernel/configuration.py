# SPDX-License-Identifier: MPL-2.0
"""Explicit deployment identity for the public source export.

Importing primitives never discovers an existing Mise installation or loads its
credentials. No activation mandate is bundled or implied by this configuration.
"""
from functools import lru_cache
import json
import os
from pathlib import Path
import re

class ConfigurationError(ValueError):
    pass


def _identity(name: str) -> str:
    value = os.environ.get(name, '')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):
        raise ConfigurationError(f'{name} must be explicitly set to an identifier')
    return value


def company_id() -> str:
    return _identity('CC_SUITE_COMPANY_ID')


def scribe_deployment_id() -> str:
    return _identity('CC_SUITE_SCRIBE_DEPLOYMENT_ID')


def authority_issuer() -> str | None:
    return deployment_binding('AUTHORITY_ISSUER')


@lru_cache(maxsize=1)
def _bindings() -> dict:
    configured = os.environ.get('CC_SUITE_AUTHORITY_BINDINGS_FILE')
    if not configured:
        return {}
    path = Path(configured)
    if not path.is_absolute() or path.is_symlink():
        raise ConfigurationError('Authority bindings must name an absolute non-symlink file')
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in value.items()):
        raise ConfigurationError('Authority bindings must be a nonempty-string mapping')
    return value


def deployment_binding(name: str) -> str | None:
    """Absent bindings deliberately fail native exact-authority comparisons."""
    return _bindings().get(name)


def identity_environment() -> dict[str, str]:
    """Nonsecret identity needed by isolated Python broker processes."""
    return {'CC_SUITE_COMPANY_ID': company_id(), 'CC_SUITE_SCRIBE_DEPLOYMENT_ID': scribe_deployment_id()}


def trusted_deployment_environment() -> dict[str, str]:
    """Only trusted supervisor processes receive the authority binding path."""
    result = identity_environment()
    if os.environ.get('CC_SUITE_AUTHORITY_BINDINGS_FILE'):
        _bindings()  # Validate before persisting the explicit configuration.
        result['CC_SUITE_AUTHORITY_BINDINGS_FILE'] = os.environ['CC_SUITE_AUTHORITY_BINDINGS_FILE']
    return result
