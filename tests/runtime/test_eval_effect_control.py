# SPDX-License-Identifier: MPL-2.0
"""Exercise the native interception algorithm against explicit synthetic adapters."""
from dataclasses import replace
from types import ModuleType
import sys
import pytest
from fleet_kernel.eval_plane.effect_control import (
    ActionClass, ChokepointSpec, ChokepointUnavailable, EffectDenied,
    EffectInterceptor, ExecutionMode, is_simulated,
)


@pytest.fixture
def adapter(monkeypatch):
    module = ModuleType('ccsuite_synthetic_effect')
    calls = []
    def write(**kwargs):
        calls.append(kwargs)
        return 'real result'
    module.write = write
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = ChokepointSpec(
        name='example.write', integration='example', action_class=ActionClass.EXTERNAL_WRITE,
        module=module.__name__, attr_path='write', is_method=False,
        describe=lambda args, kwargs: 'synthetic local effect', simulate_key='payload',
    )
    return module, calls, write, spec


def test_missing_product_registry_fails_closed():
    with pytest.raises(ChokepointUnavailable, match='Explicit adopter'):
        EffectInterceptor(ExecutionMode.DENY_EFFECTS)


def test_denial_records_redacted_evidence_and_restores_callable(adapter):
    module, calls, original, spec = adapter
    with EffectInterceptor(ExecutionMode.DENY_EFFECTS, chokepoints=[spec]) as gate:
        with pytest.raises(EffectDenied):
            module.write(api_key='synthetic-credential')
    assert calls == []
    assert module.write is original
    assert gate.ledger.entries[0].state == 'blocked'
    assert 'synthetic-credential' not in str(gate.ledger.entries[0].to_dict())
    assert len(gate.ledger.entries[0].evidence_sha256) == 64


def test_simulation_is_labeled_without_real_effect(adapter):
    module, calls, original, spec = adapter
    with EffectInterceptor(ExecutionMode.SIMULATED, chokepoints=[spec]) as gate:
        result = module.write()
    assert is_simulated(result)
    assert calls == []
    assert module.write is original
    assert gate.ledger.entries[0].state == 'simulated'


def test_partial_install_failure_restores_earlier_callable(adapter):
    module, calls, original, spec = adapter
    missing = replace(spec, name='missing.write', module='ccsuite_nonexistent_adapter')
    with pytest.raises(ChokepointUnavailable):
        with EffectInterceptor(ExecutionMode.DENY_EFFECTS, chokepoints=[spec, missing]):
            pytest.fail('incomplete adapter installation must not admit work')
    assert module.write is original
    assert calls == []
