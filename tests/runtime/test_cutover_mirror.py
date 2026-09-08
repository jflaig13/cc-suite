# SPDX-License-Identifier: MPL-2.0
"""Public cutover configuration and one shared database/relay projection."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from psycopg.conninfo import make_conninfo

from fleet_kernel import company_cutover_mirror as mirror
from fleet_kernel.company_message_plane import client as client_module
from fleet_kernel.example import disposable_company
from mcp_servers.channel_relay import kernel_plane_bridge as bridge


@pytest.fixture
def configured_bus(tmp_path, monkeypatch):
    bus = tmp_path / "synthetic-bus"
    bus.mkdir()
    monkeypatch.setenv("CC_SUITE_CHANNEL_SHARED_DIR", str(bus))
    monkeypatch.setenv("CC_SUITE_ENABLE_KERNEL_BRIDGE", "1")
    bridge._cutover_cache.clear()
    yield bus
    bridge._cutover_cache.clear()


def synthetic_client(monkeypatch):
    client = client_module.CompanyMessagePlaneClient("synthetic-no-network")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    monkeypatch.setattr(client, "_connect", lambda: conn)
    return client, conn


def test_setter_projection_is_relay_fallback_after_database_failure(configured_bus, monkeypatch):
    client, conn = synthetic_client(monkeypatch)
    mirror.write_cutover_mirror("ccpo", "file_bus_only")
    result = client.set_cutover("ccto", "dual_write", "synthetic")
    conn.commit.assert_called_once()
    assert result == client_module.CutoverState("ccto", "dual_write", "synthetic")
    expected = {"ccpo": "file_bus_only", "ccto": "dual_write"}
    assert bridge._read_mirror() == expected
    assert mirror.cutover_mirror_path() == configured_bus / "kernel_cutover.json"
    def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic offline database")
    monkeypatch.setattr(client_module, "CompanyMessagePlaneClient", unavailable)
    assert bridge.get_cutover_state("ccto") == "dual_write"
    assert bridge.get_cutover_state("ccpo") == "file_bus_only"
    assert not (Path(client_module.__file__).resolve().parents[2] / "channels/shared/kernel_cutover.json").exists()


def test_database_cutover_persists_and_offline_relay_reads_projection(configured_bus, monkeypatch):
    with disposable_company() as company:
        client = client_module.CompanyMessagePlaneClient(make_conninfo(**company.conninfo))
        client.set_cutover("ccpo", "dual_write", "synthetic cutover")
        company.restart()
        assert client.get_cutover("ccpo") == client_module.CutoverState("ccpo", "dual_write", "synthetic cutover")
    # The disposable database is now gone; fallback must still see the projection.
    monkeypatch.setattr(client_module, "CompanyMessagePlaneClient", lambda: client)
    assert bridge.get_cutover_state("ccpo") == "dual_write"


@pytest.mark.parametrize("value", [None, "relative/path", "/missing-synthetic-bus"])
def test_missing_or_invalid_bus_refuses_before_database_mutation(value, monkeypatch):
    if value is None:
        monkeypatch.delenv("CC_SUITE_CHANNEL_SHARED_DIR", raising=False)
    else:
        monkeypatch.setenv("CC_SUITE_CHANNEL_SHARED_DIR", value)
    client, conn = synthetic_client(monkeypatch)
    with pytest.raises(client_module.MessagePlaneError, match="existing absolute directory"):
        client.set_cutover("ccto", "dual_write")
    conn.execute.assert_not_called()
    assert mirror.read_cutover_mirror() == {}


def test_failed_projection_reports_committed_state_and_retry_repairs(configured_bus, monkeypatch):
    client, conn = synthetic_client(monkeypatch)
    real_locked = client_module.locked_cutover_mirror
    @contextmanager
    def unavailable_projection(*args, **kwargs):
        with real_locked(*args, **kwargs):
            def unavailable(*args, **kwargs):
                raise OSError("synthetic unavailable disk")
            yield unavailable
    monkeypatch.setattr(client_module, "locked_cutover_mirror", unavailable_projection)
    with pytest.raises(client_module.MessagePlaneError, match="committed in database.*retry"):
        client.set_cutover("ccto", "rolled_back")
    conn.commit.assert_called_once()
    monkeypatch.setattr(client_module, "locked_cutover_mirror", real_locked)
    client.set_cutover("ccto", "rolled_back")
    assert bridge._read_mirror()["ccto"] == "rolled_back"


def test_successful_database_read_survives_projection_failure(configured_bus, monkeypatch):
    client, _ = synthetic_client(monkeypatch)
    monkeypatch.setattr(client, "get_cutover", lambda role: client_module.CutoverState(role, "rolled_back", ""))
    monkeypatch.setattr(client_module, "CompanyMessagePlaneClient", lambda: client)
    mirror.write_cutover_mirror("ccto", "dual_write")
    real_locked = bridge.locked_cutover_mirror
    @contextmanager
    def unavailable_projection(*args, **kwargs):
        with real_locked(*args, **kwargs):
            def unavailable(*args, **kwargs):
                raise OSError("synthetic unavailable disk")
            yield unavailable
    monkeypatch.setattr(bridge, "locked_cutover_mirror", unavailable_projection)
    assert bridge.get_cutover_state("ccto") == "rolled_back"


def test_concurrent_role_projections_preserve_all_roles(configured_bus):
    roles = sorted(client_module.ROLES)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda role: mirror.write_cutover_mirror(role, "dual_write"), roles))
    assert mirror.read_cutover_mirror() == {role: "dual_write" for role in roles}
    assert list(configured_bus.glob(".kernel-cutover-*")) == []


def test_malformed_or_symlinked_mirror_is_inactive(configured_bus, tmp_path):
    path = mirror.cutover_mirror_path()
    path.write_text("not-json")
    assert mirror.read_cutover_mirror() == {}
    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text('{"ccto":"dual_write"}')
    path.symlink_to(outside)
    assert mirror.read_cutover_mirror() == {}
    mirror.write_cutover_mirror("ccpo", "file_bus_only")
    assert not path.is_symlink()
    assert outside.read_text() == '{"ccto":"dual_write"}'
    assert mirror.read_cutover_mirror() == {"ccpo": "file_bus_only"}


def test_delayed_relay_read_cannot_overwrite_newer_setter_projection(configured_bus, monkeypatch):
    """Hold an old canonical read while a rollback setter attempts to enter."""
    client, conn = synthetic_client(monkeypatch)
    canonical = {"state": "dual_write"}
    read_started, release_read = Event(), Event()
    setter_lock_attempted, setter_database_entered = Event(), Event()

    class DelayedReader:
        def get_cutover(self, role):
            snapshot = canonical["state"]
            read_started.set()
            assert release_read.wait(5), "test did not release the delayed read"
            return client_module.CutoverState(role, snapshot, "")

    def connect_setter():
        setter_database_entered.set()
        return conn

    def commit_rollback():
        canonical["state"] = "rolled_back"

    real_locked = client_module.locked_cutover_mirror
    @contextmanager
    def observed_setter_lock(*args, **kwargs):
        setter_lock_attempted.set()
        with real_locked(*args, **kwargs) as write:
            yield write

    monkeypatch.setattr(client, "_connect", connect_setter)
    conn.commit.side_effect = commit_rollback
    monkeypatch.setattr(client_module, "locked_cutover_mirror", observed_setter_lock)
    monkeypatch.setattr(client_module, "CompanyMessagePlaneClient", lambda: DelayedReader())
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(bridge.get_cutover_state, "ccto")
        try:
            assert read_started.wait(5)
            setter = pool.submit(client.set_cutover, "ccto", "rolled_back")
            assert setter_lock_attempted.wait(5)
            assert not setter_database_entered.wait(0.15), "setter reached DB while an older read could still project"
        finally:
            release_read.set()
        assert reader.result(timeout=5) == "dual_write"
        assert setter.result(timeout=5).state == "rolled_back"
    assert canonical["state"] == "rolled_back"
    assert mirror.read_cutover_mirror()["ccto"] == "rolled_back"


def test_same_role_setters_cannot_reverse_commit_and_projection_order(configured_bus, monkeypatch):
    """Pause the first commit before projection; the second must not enter DB."""
    first, first_conn = synthetic_client(monkeypatch)
    second, second_conn = synthetic_client(monkeypatch)
    canonical = {"state": "file_bus_only"}
    committed_first, release_first = Event(), Event()
    second_lock_attempted, second_database_entered = Event(), Event()
    trace = []

    def first_commit():
        canonical["state"] = "dual_write"
        trace.append(("commit", "dual_write"))
        committed_first.set()
        assert release_first.wait(5), "test did not release the first committed setter"

    def second_connect():
        second_database_entered.set()
        return second_conn

    def second_commit():
        canonical["state"] = "rolled_back"
        trace.append(("commit", "rolled_back"))

    real_locked = client_module.locked_cutover_mirror
    @contextmanager
    def traced_projection(*args, **kwargs):
        if committed_first.is_set():
            second_lock_attempted.set()
        with real_locked(*args, **kwargs) as write:
            def traced_write(role, state):
                write(role, state)
                trace.append(("mirror", state))
            yield traced_write

    first_conn.commit.side_effect = first_commit
    second_conn.commit.side_effect = second_commit
    monkeypatch.setattr(second, "_connect", second_connect)
    monkeypatch.setattr(client_module, "locked_cutover_mirror", traced_projection)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first.set_cutover, "ccto", "dual_write")
        try:
            assert committed_first.wait(5)
            second_result = pool.submit(second.set_cutover, "ccto", "rolled_back")
            assert second_lock_attempted.wait(5)
            assert not second_database_entered.wait(0.15), "second setter committed before the first could project"
            assert canonical["state"] == "dual_write"
        finally:
            release_first.set()
        assert first_result.result(timeout=5).state == "dual_write"
        assert second_result.result(timeout=5).state == "rolled_back"
    assert trace == [("commit", "dual_write"), ("mirror", "dual_write"),
                     ("commit", "rolled_back"), ("mirror", "rolled_back")]
    assert canonical["state"] == "rolled_back"
    assert mirror.read_cutover_mirror()["ccto"] == "rolled_back"
