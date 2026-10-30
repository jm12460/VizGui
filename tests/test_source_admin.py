"""Local source administration with disposable configuration and fake controller."""
import copy
import json
import os
import socket
import stat
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from vizgui import config, server_identity, source_admin


@pytest.fixture
def admin(tmp_path, monkeypatch):
    if not hasattr(socket, "SO_PEERCRED"):
        pytest.skip("Linux peer credentials unavailable")
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    directory = tmp_path / "config"
    directory.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(config, "CONFIG_DIR", str(directory))
    monkeypatch.setattr(config, "CONFIG_PATH", str(directory / "config.json"))
    initial = {"sources": [{"name": "NAS", "kind": "smb", "smb": {
        "server": "test-nas", "share": "test-share", "password": "test-password"}}],
        "targets": [{"type": "cast", "auth_token": "test-cast-token"}],
        "web": {"token": "test-remote-token", "pin": "123456"}, "auto_next": True, "unknown": {"keep": True}}
    monkeypatch.setattr(config, "CFG", copy.deepcopy(initial))
    config.save_config(initial)
    identity = {"id": "test-server", "instance_id": "test-process", "ok": True}
    monkeypatch.setattr(server_identity, "server_info", lambda: identity)
    controller = SimpleNamespace(engine=SimpleNamespace(_sources_revision=0),
                                 call=lambda fn, *args, **kwargs: fn(*args))
    handle = source_admin.start(controller)
    try:
        yield identity, initial, runtime, controller
    finally:
        source_admin.close(handle)


def test_add_remove_preserves_other_config_and_never_returns_credentials(admin, tmp_path):
    identity, original, runtime, controller = admin
    listed = source_admin.request("127.0.0.1", identity, "list")
    serialized = json.dumps(listed)
    assert all(secret not in serialized for secret in ("test-password", "test-cast-token", "test-remote-token"))
    assert stat.S_IMODE(os.stat(runtime / source_admin.SOCKET_NAME).st_mode) == 0o600
    folder = tmp_path / "media"
    folder.mkdir()
    added = source_admin.request("127.0.0.1", identity, "add", revision=0,
                                 source={"kind": "local", "path": str(folder)})
    assert added["ok"] and added["revision"] == 1
    assert controller.engine._sources_revision == 1
    assert config.CFG["web"] == original["web"]
    assert config.CFG["targets"] == original["targets"]
    assert config.CFG["unknown"] == original["unknown"]
    assert config.CFG["sources"][0] == original["sources"][0]
    stale = source_admin.request("127.0.0.1", identity, "remove", revision=0, index=0)
    assert not stale["ok"]
    removed = source_admin.request("127.0.0.1", identity, "remove", revision=1, index=1)
    assert removed["ok"] and folder.exists()
    assert config.CFG == original
    assert stat.S_IMODE(os.stat(config.CONFIG_PATH).st_mode) == 0o600


def test_smb_add_never_returns_password_or_mounts(admin, monkeypatch):
    identity, _, _, _ = admin
    mount = Mock(side_effect=AssertionError("unexpected mount"))
    monkeypatch.setattr("vizgui.sources.smb_mount", mount)
    result = source_admin.request("localhost", identity, "add", revision=0,
                                  source={"kind": "smb", "name": "New", "smb": {
                                      "server": "test", "share": "media", "password": "new-test-secret"}})
    assert result["ok"]
    assert "new-test-secret" not in json.dumps(result)
    assert config.CFG["sources"][-1]["smb"]["password"] == "new-test-secret"
    mount.assert_not_called()


def test_external_config_change_and_save_failure_do_not_overwrite(admin, tmp_path, monkeypatch):
    identity, _, _, _ = admin
    folder = tmp_path / "media"
    folder.mkdir()
    external = copy.deepcopy(config.CFG)
    external["unknown"]["keep"] = "changed elsewhere"
    config.save_config(external)
    result = source_admin.request("localhost", identity, "add", revision=0,
                                  source={"path": str(folder)})
    assert not result["ok"]
    assert json.loads(open(config.CONFIG_PATH).read()) == external
    config.save_config(config.CFG)
    monkeypatch.setattr(config, "save_config", Mock(side_effect=OSError("test-password")))
    result = source_admin.request("localhost", identity, "add", revision=0,
                                  source={"path": str(folder)})
    assert not result["ok"] and "test-password" not in json.dumps(result)
    assert json.loads(open(config.CONFIG_PATH).read()) == config.CFG


def test_remote_host_old_instance_and_foreign_uid_are_rejected(admin):
    identity, _, _, _ = admin
    with pytest.raises(RuntimeError, match="server machine"):
        source_admin.request("192.0.2.1", identity, "list")
    stale = source_admin.request("localhost", {**identity, "instance_id": "old"}, "list")
    assert not stale["ok"]
    foreign = Mock()
    foreign.getsockopt.return_value = struct.pack("3i", 123, os.getuid() + 1, 123)
    assert not source_admin._same_user(foreign)


def test_existing_live_socket_is_not_replaced(admin):
    _, _, runtime, controller = admin
    before = os.lstat(runtime / source_admin.SOCKET_NAME).st_ino
    with pytest.raises((OSError, RuntimeError)):
        source_admin.start(controller)
    assert os.lstat(runtime / source_admin.SOCKET_NAME).st_ino == before


def test_backfilled_defaults_do_not_block_legacy_config(admin, tmp_path):
    identity, _, _, _ = admin
    old = copy.deepcopy(config.CFG)
    del old["auto_next"]
    config.save_config(old)
    folder = tmp_path / "media"
    folder.mkdir()
    result = source_admin.request("localhost", identity, "add", revision=0,
                                  source={"path": str(folder)})
    assert result["ok"]
    assert config.CFG["auto_next"] is True


def test_rejected_peer_cannot_read_or_write_sources(admin, monkeypatch):
    identity, original, _, _ = admin
    monkeypatch.setattr(source_admin, "_same_user", lambda _: False)
    with pytest.raises((OSError, RuntimeError)):
        source_admin.request("localhost", identity, "list")
    assert config.CFG == original


def test_invalid_runtime_directory_and_socket_are_not_replaced(admin, monkeypatch, tmp_path):
    _, _, _, controller = admin
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(unsafe))
    with pytest.raises(RuntimeError, match="not private"):
        source_admin.socket_path()
    unsafe.chmod(0o700)
    path = unsafe / source_admin.SOCKET_NAME
    path.write_text("leave this alone")
    with pytest.raises(RuntimeError, match="Unsafe existing"):
        source_admin.start(controller)
    assert path.read_text() == "leave this alone"


def test_stale_socket_is_recovered_without_touching_other_files(tmp_path, monkeypatch):
    if not hasattr(socket, "SO_PEERCRED"):
        pytest.skip("Linux peer credentials unavailable")
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    path = runtime / source_admin.SOCKET_NAME
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    marker = runtime / "unrelated"
    marker.write_text("preserve")
    controller = SimpleNamespace(engine=SimpleNamespace(_sources_revision=0),
                                 call=lambda fn, *args, **kwargs: fn(*args))
    handle = source_admin.start(controller)
    try:
        assert stat.S_ISSOCK(os.lstat(path).st_mode)
        assert marker.read_text() == "preserve"
    finally:
        source_admin.close(handle)


def test_expired_queued_request_cannot_change_configuration(admin, tmp_path):
    import time
    identity, original, _, _ = admin
    folder = tmp_path / "media"
    folder.mkdir()
    manager = source_admin.SourceManager()
    request = {"server_id": identity["id"], "instance_id": identity["instance_id"],
               "action": "add", "revision": 0}
    result = manager.handle(request, {"name": "media", "path": str(folder)},
                            deadline=time.monotonic() - 1)
    assert not result["ok"]
    assert config.CFG == original
